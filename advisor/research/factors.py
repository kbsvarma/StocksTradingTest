"""Cross-sectional factor engine — literature-grounded, sector-neutral, regime-aware.

Computes for every liquid name in the cached panel:

  mom_12_1   12-month momentum excluding last month   (Jegadeesh-Titman 1993)
  rev_1m     1-month short-term reversal              (Jegadeesh 1990, Lehmann 1990)
  resid_mom  6-month residual momentum vs sector ETF  (Blitz-Huij-Martens 2011)
  prox_52w   proximity to 52-week high                (George-Hwang 2004)
  lowvol     negative 60d realized vol                (Ang et al 2006 low-vol anomaly)
  turn_anom  20d/120d volume ratio (abnormal attention/turnover)
  shock      recent outsized move flag (|1d| > 2.5σ in last 10d) — PEAD-style
             drift candidates, surfaced for the analyst loop, not scored

Method: winsorized (±3) z-scores computed WITHIN GICS sector (sector-neutral),
combined with REGIME-CONDITIONED weights — momentum is de-weighted and
reversal/low-vol up-weighted when the vol/credit regime turns, per the
momentum-crash literature (Daniel-Moskowitz 2016). Liquidity floor:
median 21d dollar volume > $10M and price > $5.

Output: ranked LONG and SHORT sheets with full factor attribution per name.

CLI: python -m advisor.research.factors [--json PATH] [--top 20]
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

WEIGHTS = {
    "risk_on": {"mom_12_1": .30, "resid_mom": .20, "prox_52w": .15,
                "rev_1m": .10, "lowvol": .05, "turn_anom": .05},
    "neutral": {"mom_12_1": .20, "resid_mom": .15, "prox_52w": .10,
                "rev_1m": .20, "lowvol": .15, "turn_anom": .05},
    "stress":  {"mom_12_1": .08, "resid_mom": .07, "prox_52w": .05,
                "rev_1m": .30, "lowvol": .30, "turn_anom": .05},
}


def detect_regime(close) -> dict:
    """Regime from SPY trend, VIX level + term structure, credit, breadth."""
    spy = close["SPY"].dropna()
    vix = float(close["^VIX"].dropna().iloc[-1])
    try:
        term = vix / float(close["^VIX3M"].dropna().iloc[-1])
    except Exception:
        term = 0.9
    trend_up = float(spy.iloc[-1]) > float(spy.rolling(200).mean().iloc[-1])
    try:
        hyg = close["HYG"].dropna()
        credit_21d = (float(hyg.iloc[-1]) / float(hyg.iloc[-22]) - 1) * 100
    except Exception:
        credit_21d = 0.0
    if vix > 26 or term > 1.0 or credit_21d < -2.0 or not trend_up:
        name = "stress" if (vix > 26 or term > 1.0 or credit_21d < -2.0) else "neutral"
    elif vix < 20 and term < 0.95 and trend_up:
        name = "risk_on"
    else:
        name = "neutral"
    return {"name": name, "vix": round(vix, 1), "vix_term": round(term, 2),
            "spy_above_200dma": trend_up, "hyg_21d_pct": round(credit_21d, 1),
            "weights": WEIGHTS[name]}


def compute(top: int = 20) -> dict:
    import numpy as np
    import pandas as pd
    from advisor.research.datastore import load_panel, panel_age_hours
    from advisor.research.universe import load as load_universe

    age = panel_age_hours()
    u = load_universe()
    sectors = u["stocks"]
    close, volume = load_panel("close"), load_panel("volume")
    rets = close.pct_change()

    stock_cols = [c for c in close.columns if c in sectors]
    c = close[stock_cols]
    r = rets[stock_cols]
    v = volume[stock_cols]

    # ── Liquidity floor ──────────────────────────────────────────────────
    dollar_vol = (c * v).rolling(21, min_periods=10).median().iloc[-1]
    px = c.iloc[-1]
    liquid = dollar_vol[(dollar_vol > 1e7) & (px > 5)].index
    c, r, v = c[liquid], r[liquid], v[liquid]

    # ── Raw factors ──────────────────────────────────────────────────────
    f = pd.DataFrame(index=liquid)
    f["mom_12_1"] = c.iloc[-21] / c.iloc[-252] - 1
    f["rev_1m"] = -(c.iloc[-1] / c.iloc[-21] - 1)
    f["prox_52w"] = c.iloc[-1] / c.rolling(252, min_periods=120).max().iloc[-1]
    f["lowvol"] = -r.rolling(60, min_periods=40).std().iloc[-1]
    f["turn_anom"] = (v.rolling(20, min_periods=10).mean().iloc[-1]
                      / v.rolling(120, min_periods=60).mean().iloc[-1])

    # Residual momentum vs sector ETF (vectorized per sector, 126d window,
    # excluding last 21d)
    f["resid_mom"] = np.nan
    win = r.tail(126)
    for sec, etf in u["sector_etf"].items():
        names = [t for t in liquid if sectors.get(t) == sec]
        if not names or etf not in rets.columns:
            continue
        re = rets[etf].tail(126)
        sub = win[names]
        beta = sub.apply(lambda s: s.cov(re) / re.var() if re.var() else 0.0)
        resid = sub.sub(np.outer(re, beta), axis=0)
        f.loc[names, "resid_mom"] = (1 + resid.iloc[:-21]).prod() - 1

    # Shock flag (PEAD-style candidates for the analyst loop)
    sigma = r.rolling(20).std()
    recent = r.tail(10)
    shock_mask = (recent.abs() > 2.5 * sigma.tail(10)).any()
    shock_dir = np.sign(recent.where(recent.abs() > 2.5 * sigma.tail(10)).sum())

    # ── Sector-neutral winsorized z-scores ───────────────────────────────
    sec_series = pd.Series({t: sectors.get(t, "?") for t in f.index})
    z = pd.DataFrame(index=f.index)
    for col in f.columns:
        z[col] = (f[col].groupby(sec_series)
                  .transform(lambda s: ((s - s.mean()) / (s.std() or 1)).clip(-3, 3)))

    regime = detect_regime(close)
    w = regime["weights"]
    composite = sum(z[k].fillna(0) * wt for k, wt in w.items())

    def sheet(idx) -> list[dict]:
        out = []
        for t in idx:
            out.append({
                "ticker": t, "sector": sectors.get(t, "?"),
                "px": round(float(px[t]), 2),
                "score": round(float(composite[t]), 2),
                "attribution": {k: round(float(z.loc[t, k]), 2) if pd.notna(z.loc[t, k]) else None
                                for k in w},
                "raw": {"mom_12_1_pct": round(float(f.loc[t, "mom_12_1"]) * 100, 1),
                        "ret_1m_pct": round(-float(f.loc[t, "rev_1m"]) * 100, 1),
                        "pct_of_52w_high": round(float(f.loc[t, "prox_52w"]) * 100, 1),
                        "rv60_ann_pct": round(-float(f.loc[t, "lowvol"]) * (252 ** .5) * 100, 0),
                        "dollar_vol_21d_m": round(float(dollar_vol[t]) / 1e6, 1)},
                "shock": bool(shock_mask.get(t, False)),
                "shock_dir": int(shock_dir.get(t, 0) or 0),
            })
        return out

    ranked = composite.dropna().sort_values(ascending=False)
    shocks = [t for t in liquid if shock_mask.get(t, False)]
    return {
        "as_of": datetime.now(ET).isoformat(),
        "panel_age_hours": round(age, 1),
        "n_universe": len(stock_cols), "n_liquid": len(liquid),
        "regime": regime,
        "longs": sheet(ranked.head(top).index),
        "shorts": sheet(ranked.tail(max(top // 2, 5)).index[::-1]),
        "shock_candidates": sheet([t for t in ranked.index if t in shocks][:15]),
        "method": ("sector-neutral winsorized z-scores; regime-conditioned weights "
                   f"({regime['name']}); liquidity floor $10M ADV, px>$5"),
    }


def render(s: dict) -> str:
    L = [f"FACTOR SHEET  {s['as_of']}   regime={s['regime']['name'].upper()} "
         f"(VIX {s['regime']['vix']}, term {s['regime']['vix_term']}, "
         f"HYG21d {s['regime']['hyg_21d_pct']}%)",
         f"universe {s['n_universe']} → liquid {s['n_liquid']}   "
         f"panel age {s['panel_age_hours']}h",
         f"method: {s['method']}", ""]
    for label, rowsk in (("LONG CANDIDATES", "longs"), ("SHORT CANDIDATES", "shorts"),
                         ("EARNINGS/SHOCK DRIFT CANDIDATES", "shock_candidates")):
        L.append(f"== {label} ==")
        L.append(f"{'tkr':<7}{'px':>9}{'score':>7}  {'sector':<24}"
                 f"{'mom12':>7}{'r1m':>7}{'%52wH':>7}{'rv60':>6}{'$vol(M)':>9}  attribution(z)")
        for x in s[rowsk]:
            a = x["attribution"]; rw = x["raw"]
            att = " ".join(f"{k.split('_')[0]}:{v:+.1f}" for k, v in a.items() if v is not None)
            L.append(f"{x['ticker']:<7}{x['px']:>9}{x['score']:>7.2f}  {x['sector']:<24}"
                     f"{rw['mom_12_1_pct']:>7}{rw['ret_1m_pct']:>7}{rw['pct_of_52w_high']:>7}"
                     f"{rw['rv60_ann_pct']:>6.0f}{rw['dollar_vol_21d_m']:>9}  {att}"
                     + ("  ⚡shock" if x["shock"] else ""))
        L.append("")
    return "\n".join(L)


def main() -> int:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 20
    s = compute(top=top)
    print(render(s))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2))
        print(f"[factors] json → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
