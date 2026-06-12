"""Factor validation — information coefficients against forward returns.

THE materiality test of the signal stack: for historical as-of dates across
the cached panel, compute each factor's sector-neutral z (same code path as
live — advisor.research.factors.raw_factors) and the Spearman rank
correlation (IC) with the NEXT 21 trading days' returns. Non-overlapping
21d steps. Reports mean IC, t-stat, and top-decile minus universe spread
per factor and for the current regime-weighted composites.

Honesty notes baked into the output: ~2y of panel → ~20 non-overlapping
periods → low statistical power. This detects signals that are clearly
dead or clearly material; it cannot fine-tune weights. Survivorship caveat:
the universe is TODAY'S S&P 1500 (free-data limitation) — momentum/quality
ICs are likely flattered slightly by index-survivor bias.

CLI: python -m advisor.research.validate [--step 21] [--json PATH]
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")
FWD = 21  # forward horizon, trading days


def run(step: int = 21) -> dict:
    import numpy as np
    import pandas as pd
    from scipy.stats import spearmanr

    from advisor.research.datastore import load_panel
    from advisor.research.factors import WEIGHTS, raw_factors
    from advisor.research.universe import load as load_universe

    u = load_universe()
    close, volume = load_panel("close"), load_panel("volume")
    rets = close.pct_change()
    n = len(close)

    # as-of indices: need >=260 history behind, FWD ahead; step apart
    asofs = list(range(265, n - FWD, step))
    factor_ics: dict[str, list] = {}
    comp_ics: dict[str, list] = {k: [] for k in WEIGHTS}
    decile_spread: list = []

    for k in asofs:
        cl, vo, re = close.iloc[:k], volume.iloc[:k], rets.iloc[:k]
        try:
            f, z, liquid, *_ = raw_factors(cl, vo, re, u)
        except Exception:
            continue
        fwd = close.iloc[k + FWD - 1] / close.iloc[k - 1] - 1
        fwd = fwd.reindex(liquid).dropna()
        zz = z.loc[fwd.index]
        for col in z.columns:
            pair = pd.concat([zz[col], fwd], axis=1).dropna()
            if len(pair) > 100:
                ic = spearmanr(pair.iloc[:, 0], pair.iloc[:, 1]).statistic
                factor_ics.setdefault(col, []).append(ic)
        for wname, w in WEIGHTS.items():
            comp = sum(zz[c2].fillna(0) * wt for c2, wt in w.items())
            pair = pd.concat([comp, fwd], axis=1).dropna()
            if len(pair) > 100:
                comp_ics[wname].append(spearmanr(pair.iloc[:, 0], pair.iloc[:, 1]).statistic)
                top = pair[pair.iloc[:, 0] >= pair.iloc[:, 0].quantile(0.9)].iloc[:, 1].mean()
                decile_spread.append(top - pair.iloc[:, 1].mean()) if wname == "risk_on" else None

    def stats(vals):
        if not vals:
            return None
        a = np.array(vals)
        return {"mean_ic": round(float(a.mean()), 4),
                "t_stat": round(float(a.mean() / (a.std(ddof=1) / len(a) ** .5)), 2)
                if len(a) > 2 and a.std(ddof=1) > 0 else None,
                "n_periods": len(a),
                "pct_positive": round(float((a > 0).mean() * 100), 0)}

    return {
        "as_of": datetime.now(ET).isoformat(),
        "horizon_days": FWD, "step_days": step, "n_eval_dates": len(asofs),
        "factors": {k: stats(v) for k, v in factor_ics.items()},
        "composites": {k: stats(v) for k, v in comp_ics.items()},
        "risk_on_top_decile_excess_21d_pct":
            round(float(np.mean(decile_spread)) * 100, 2) if decile_spread else None,
        "caveats": ["~20 non-overlapping periods — low power; only clear life/death calls",
                    "today's index membership → survivorship flatters momentum slightly",
                    "IC = Spearman(z, fwd 21d ret) on liquid universe at each date"],
    }


def render(v: dict) -> str:
    L = [f"FACTOR VALIDATION (IC)  {v['as_of'][:16]}  horizon={v['horizon_days']}d "
         f"step={v['step_days']}d  dates={v['n_eval_dates']}"]
    L.append(f"{'factor':<12}{'meanIC':>8}{'t':>6}{'n':>4}{'%pos':>6}")
    for k, s in sorted((v.get("factors") or {}).items(),
                       key=lambda kv: -(kv[1] or {}).get("mean_ic", 0)):
        if s:
            L.append(f"{k:<12}{s['mean_ic']:>8}{str(s['t_stat']):>6}{s['n_periods']:>4}"
                     f"{s['pct_positive']:>6.0f}")
    L.append("composites (regime weightings applied unconditionally):")
    for k, s in (v.get("composites") or {}).items():
        if s:
            L.append(f"  {k:<10} meanIC={s['mean_ic']}  t={s['t_stat']}  "
                     f"%pos={s['pct_positive']:.0f}")
    if v.get("risk_on_top_decile_excess_21d_pct") is not None:
        L.append(f"top-decile excess (risk_on comp): "
                 f"{v['risk_on_top_decile_excess_21d_pct']:+.2f}%/21d")
    for c in v["caveats"]:
        L.append(f"  ⚠ {c}")
    return "\n".join(L)


if __name__ == "__main__":
    step = int(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else 21
    v = run(step=step)
    print(render(v))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.write_text(json.dumps(v, indent=2))
