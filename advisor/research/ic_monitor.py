"""Live IC monitor — matures nightly factor snapshots into an out-of-sample
IC series. The honest replacement for the frozen 11-period backtest: every
factor_history/ snapshot older than 21 trading days gets scored against the
realized forward returns that have since happened.

Drift rule (TUNING_NOTES, quant-panel): weight-change PROPOSALS only on
EWMA(8w-halflife) sign flip or >1-sigma drop sustained 3 consecutive weeks
AND |t| > 2.0 — and only the user applies them. This tool only reports.

CLI: python -m advisor.research.ic_monitor [--json]
Writes advisor/data/research/ic_live.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
FWD = 21
OUT = RESEARCH_DIR / "ic_live.json"
HIST_DIR = RESEARCH_DIR / "factor_history"


def mature() -> dict:
    import pandas as pd
    from scipy.stats import spearmanr

    try:
        prior = json.loads(OUT.read_text())
        scored = {r["snapshot"] for r in prior.get("series", [])}
        series = prior.get("series", [])
    except Exception:
        scored, series = set(), []

    from advisor.research.datastore import load_panel
    close = load_panel("close")
    dates = list(close.index)

    n_new = 0
    for snap_path in sorted(HIST_DIR.glob("*.parquet")):
        day = snap_path.stem
        if day in scored:
            continue
        try:
            snap_date = pd.Timestamp(day)
            pos = close.index.searchsorted(snap_date, side="right") - 1
            if pos < 0 or pos + FWD >= len(dates):
                continue                        # not matured yet
            snap = pd.read_parquet(snap_path)
            fwd = (close.iloc[pos + FWD] / close.iloc[pos] - 1)
            fwd = fwd.reindex(snap.index).dropna()
            row = {"snapshot": day, "fwd_end": str(dates[pos + FWD].date()),
                   "n": len(fwd), "regime": (snap.get("regime").iloc[0]
                                             if "regime" in snap else None),
                   "ic": {}}
            factor_cols = [c for c in snap.columns
                           if c not in ("px", "sector", "regime", "shock")]
            for col in factor_cols:
                x = snap[col].reindex(fwd.index)
                ok = x.notna()
                if ok.sum() < 50:
                    continue
                ic, _p = spearmanr(x[ok], fwd[ok])
                row["ic"][col] = round(float(ic), 4)
            series.append(row)
            n_new += 1
        except Exception as exc:
            print(f"[ic_monitor] {day}: {exc}", file=sys.stderr)

    # trailing stats per factor over the matured series
    stats: dict[str, dict] = {}
    if series:
        import numpy as np
        factors = sorted({k for r in series for k in r["ic"]})
        for k in factors:
            vals = [r["ic"][k] for r in series if k in r["ic"]]
            if not vals:
                continue
            arr = np.array(vals)
            # EWMA with ~8-week halflife (snapshots are daily; 40 trading days)
            w = 0.5 ** (np.arange(len(arr))[::-1] / 40)
            stats[k] = {
                "n_obs": len(arr),
                "mean_ic": round(float(arr.mean()), 4),
                "ewma_ic": round(float((arr * w).sum() / w.sum()), 4),
                "t_stat": round(float(arr.mean() / (arr.std(ddof=1) / len(arr) ** .5)), 2)
                if len(arr) > 2 and arr.std(ddof=1) > 0 else None,
                "pct_positive": round(float((arr > 0).mean()), 2),
            }

    out = {"as_of": datetime.now(ET).isoformat(),
           "fwd_days": FWD, "n_matured": len(series), "n_new_this_run": n_new,
           "factors": stats, "series": series[-260:],
           "note": "overlapping daily snapshots — t-stats overstated by "
                   "~sqrt(21); use the non-overlapping weekly view in "
                   "validate2 for inference. Proposals only per TUNING_NOTES "
                   "drift rule; user applies."}
    OUT.write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    res = mature()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2))
    else:
        print(f"ic_monitor: {res['n_matured']} matured snapshots "
              f"(+{res['n_new_this_run']} new)")
        for k, s in res.get("factors", {}).items():
            print(f"  {k:<12} mean {s['mean_ic']:+.4f}  ewma {s['ewma_ic']:+.4f} "
                  f" t {s['t_stat']}  +ve {s['pct_positive']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
