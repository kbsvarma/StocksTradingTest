"""Fundamental/event factor scores — PROSPECTIVE ONLY (zero composite weight).

Computes from the PIT snapshot stores (never live scrapes):
  sue           last earnings surprise z-scored vs own 8-quarter history,
                active <=60d post-report (Bernard-Thomas PEAD lineage)
  pead          signed drift carrier: surprise direction x recency decay
  est_revision  revision breadth (up-down counts, 30d) + eps-trend 30d delta
                (Yahoo's own lookback fields — usable day 1; our accrued
                snapshots cross-validate them over time)
  value         earnings yield (1/fwdPE) + FCF yield, winsorized z
  quality       ROE + gross margin + earnings-growth, winsorized z
  squeeze_flag  short%float > 15 AND positive revision breadth (flag only)

GATE (TUNING_NOTES): none of these enter the composite until ic_monitor
shows live IC > 0 with |t| >= 1.5 over >=13 weeks AND the user approves a
starter weight. Until then they nominate candidates and annotate sheets.

CLI: python -m advisor.research.factors_fundamental [--top 15]
Writes advisor/data/research/fundamental_latest.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "fundamental_latest.json"


def _z(s):
    return ((s - s.mean()) / (s.std() or 1)).clip(-3, 3)


def _latest(dirpath):
    files = sorted(dirpath.glob("dt=*.parquet"))
    return files[-1] if files else None


def compute_scores():
    """Returns (DataFrame indexed by ticker, meta dict). Never raises —
    empty frame when stores are missing (first nights)."""
    import numpy as np
    import pandas as pd

    meta = {"as_of": datetime.now(ET).isoformat(), "inputs": {}}
    f = pd.DataFrame()

    info_p = _latest(RESEARCH_DIR / "snapshots" / "info")
    est_p = _latest(RESEARCH_DIR / "estimates")
    hist_p = RESEARCH_DIR / "events" / "earnings_history.parquet"

    if info_p is not None:
        info = pd.read_parquet(info_p).set_index("ticker")
        meta["inputs"]["info"] = info_p.stem
        f = pd.DataFrame(index=info.index)
        # value: earnings yield + FCF yield
        ey = 1.0 / info.forwardPE.where(info.forwardPE > 0)
        fcfy = info.freeCashflow / info.marketCap.where(info.marketCap > 0)
        f["value"] = (_z(ey) + _z(fcfy.where(fcfy.abs() < 1))) / 2
        # quality: ROE + gross margin + growth (each winsorized z)
        f["quality"] = (_z(info.returnOnEquity.where(info.returnOnEquity.abs() < 3))
                        + _z(info.grossMargins)
                        + _z(info.earningsGrowth.where(info.earningsGrowth.abs() < 3))) / 3
        f["short_pct_float"] = info.shortPercentOfFloat * 100

    if est_p is not None and len(f):
        est = pd.read_parquet(est_p).set_index("ticker")
        meta["inputs"]["estimates"] = est_p.stem
        up = est.get("epsrev_0y_upLast30days", pd.Series(dtype=float)).reindex(f.index)
        dn = est.get("epsrev_0y_downLast30days", pd.Series(dtype=float)).reindex(f.index)
        breadth = (up.fillna(0) - dn.fillna(0)) / (up.fillna(0) + dn.fillna(0)).replace(0, np.nan)
        cur = est.get("epstrend_0y_current", pd.Series(dtype=float)).reindex(f.index)
        ago = est.get("epstrend_0y_30daysAgo", pd.Series(dtype=float)).reindex(f.index)
        delta = (cur / ago - 1).where(ago.abs() > 0.01).clip(-1, 1)
        f["est_revision"] = (_z(breadth) + _z(delta)) / 2
        f["squeeze_flag"] = (f.get("short_pct_float", 0) > 15) & (breadth > 0)

    if hist_p.exists() and len(f):
        hist = pd.read_parquet(hist_p)
        meta["inputs"]["earnings_history"] = "yes"
        today = datetime.now(ET).date()
        sue, pead, days_since = {}, {}, {}
        for t, g in hist.groupby("ticker"):
            g = g.dropna(subset=["surprise_pct"]).sort_values("date")
            if not len(g):
                continue
            last = g.iloc[-1]
            try:
                d = (today - datetime.fromisoformat(last["date"]).date()).days
            except (ValueError, TypeError):
                continue
            days_since[t] = d
            if d > 60:
                continue                      # drift decayed — inactive
            prior = g.surprise_pct.iloc[:-1].tail(8)
            sd = prior.std() if len(prior) >= 4 else None
            zval = (last.surprise_pct - prior.mean()) / sd if sd and sd > 0 \
                else last.surprise_pct / 10.0     # sparse-history fallback
            decay = max(0.0, 1 - d / 60)
            sue[t] = max(-3, min(3, zval))
            pead[t] = max(-3, min(3, zval)) * decay
        f["sue"] = pd.Series(sue).reindex(f.index)
        f["pead"] = pd.Series(pead).reindex(f.index)
        f["days_since_report"] = pd.Series(days_since).reindex(f.index)

    return f, meta


def render_top(f, meta, top: int = 15) -> dict:
    import pandas as pd
    out = {"as_of": meta["as_of"], "inputs": meta["inputs"],
           "gate": "PROSPECTIVE — zero composite weight until IC gate passes "
                   "(TUNING_NOTES); candidate nomination only"}
    if not len(f):
        out["status"] = "stores not built yet — first nights accrue"
        return out

    def sheet(col, ascending=False, extra=()):
        if col not in f:
            return []
        s = f[col].dropna().sort_values(ascending=ascending).head(top)
        rows = []
        for t, v in s.items():
            row = {"ticker": t, col: round(float(v), 2)}
            for e in extra:
                ev = f.loc[t].get(e)
                if ev is not None and pd.notna(ev):
                    row[e] = round(float(ev), 2) if isinstance(ev, float) else ev
            rows.append(row)
        return rows

    out["revision_leaders"] = sheet("est_revision", extra=("value", "quality"))
    out["pead_fresh"] = [r for r in sheet("pead", extra=("sue", "days_since_report"))
                         if abs(r.get("pead", 0)) >= 1.0]
    out["cheap_quality"] = sheet("value", extra=("quality",))
    # cheap AND good: joint 70th percentile screen
    try:
        vq = f[["value", "quality"]].dropna()
        v70, q70 = vq.value.quantile(0.7), vq.quality.quantile(0.7)
        joint = vq[(vq.value > v70) & (vq.quality > q70)]
        out["cheap_quality"] = [
            {"ticker": t, "value": round(float(r.value), 2),
             "quality": round(float(r.quality), 2)}
            for t, r in joint.sort_values("value", ascending=False).head(top).iterrows()]
    except Exception:
        pass
    try:
        out["squeeze_flags"] = [
            {"ticker": t, "short_pct_float": round(float(f.loc[t, "short_pct_float"]), 1)}
            for t in f.index[f.get("squeeze_flag", False) == True][:10]]  # noqa: E712
    except Exception:
        out["squeeze_flags"] = []
    return out


def main() -> int:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 15
    f, meta = compute_scores()
    res = render_top(f, meta, top)
    OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps({k: (v if not isinstance(v, list) else f"{len(v)} rows")
                      for k, v in res.items()}, indent=2))
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
