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
    comp_series: list = []   # (k, risk_on composite) for decay/turnover study

    pead: list = []   # signed drift of shock-flagged names (dir * fwd ret)

    for k in asofs:
        cl, vo, re = close.iloc[:k], volume.iloc[:k], rets.iloc[:k]
        try:
            f, z, liquid, dv_, px_, shock_mask, shock_dir = raw_factors(cl, vo, re, u)
        except Exception:
            continue
        fwd = close.iloc[k + FWD - 1] / close.iloc[k - 1] - 1
        fwd = fwd.reindex(liquid).dropna()
        zz = z.loc[fwd.index]
        # PEAD test: shock names' forward return SIGNED by shock direction,
        # minus universe mean (does the move keep drifting its own way?)
        shocked = [t for t in fwd.index
                   if shock_mask.get(t, False) and shock_dir.get(t, 0)]
        if len(shocked) >= 5:
            signed = float(np.mean([shock_dir[t] * fwd[t] for t in shocked]))
            pead.append(signed - abs(float(fwd.mean())))
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
                if wname == "risk_on":
                    comp_series.append((k, comp))

    def stats(vals):
        if not vals:
            return None
        a = np.array(vals)
        return {"mean_ic": round(float(a.mean()), 4),
                "t_stat": round(float(a.mean() / (a.std(ddof=1) / len(a) ** .5)), 2)
                if len(a) > 2 and a.std(ddof=1) > 0 else None,
                "n_periods": len(a),
                "pct_positive": round(float((a > 0).mean() * 100), 0)}

    # ── Walk-forward top-20 portfolio vs SPY (strongest evidence tier) ───
    # Equal-weight the top 20 composite names at each as-of date, hold 21
    # trading days, chain. Costs ignored (21d holds, liquid names — sub-0.1%
    # round trip; noted, not modeled). This is what the IC abstracts.
    port_rets, spy_rets = [], []
    spy = close["SPY"]
    for k, comp in comp_series:
        top20 = comp.sort_values(ascending=False).head(20).index
        fwd_names = (close.iloc[k + FWD - 1][top20] / close.iloc[k - 1][top20] - 1).dropna()
        if len(fwd_names) >= 15:
            port_rets.append(float(fwd_names.mean()))
            spy_rets.append(float(spy.iloc[k + FWD - 1] / spy.iloc[k - 1] - 1))
    wf = None
    if port_rets:
        p, s_ = np.array(port_rets), np.array(spy_rets)
        # beta-adjust: momentum top-20 is high-beta; raw excess in a bull tape
        # overstates skill. alpha = mean(p - beta*s) per 21d.
        beta = float(np.polyfit(s_, p, 1)[0]) if len(p) > 3 else 1.0
        alpha_21d = float((p - beta * s_).mean())
        wf = {"beta_vs_spy": round(beta, 2),
              "alpha_per_21d_pct": round(alpha_21d * 100, 2),
              "n_periods": len(p),
              "total_return_pct": round(float((1 + p).prod() - 1) * 100, 1),
              "spy_total_pct": round(float((1 + s_).prod() - 1) * 100, 1),
              "mean_excess_per_21d_pct": round(float((p - s_).mean()) * 100, 2),
              "excess_t_stat": round(float((p - s_).mean() / ((p - s_).std(ddof=1)
                                     / len(p) ** .5)), 2) if len(p) > 2 else None,
              "worst_period_pct": round(float(p.min()) * 100, 1),
              "pct_periods_beat_spy": round(float((p > s_).mean() * 100), 0),
              "note": "top-20 equal-weight, 21d rebalance, costs ~sub-0.1%/RT not modeled"}

    # ── Signal decay / turnover: how sticky are the ranks? ───────────────
    rank_autocorr, decile_retention = [], []
    for (k1, c1), (k2, c2) in zip(comp_series, comp_series[1:]):
        common = c1.index.intersection(c2.index)
        if len(common) > 200:
            rank_autocorr.append(spearmanr(c1[common], c2[common]).statistic)
            top1 = set(c1[common][c1[common] >= c1[common].quantile(0.9)].index)
            top2 = set(c2[common][c2[common] >= c2[common].quantile(0.9)].index)
            if top1:
                decile_retention.append(len(top1 & top2) / len(top1))

    pead_stats = None
    if pead:
        a = np.array(pead)
        pead_stats = {"mean_signed_drift_excess_21d_pct": round(float(a.mean()) * 100, 2),
                      "t_stat": round(float(a.mean() / (a.std(ddof=1) / len(a) ** .5)), 2)
                      if len(a) > 2 and a.std(ddof=1) > 0 else None,
                      "n_periods": len(a),
                      "note": "shock-direction-signed fwd 21d ret minus universe — "
                              "tests whether >2.5σ moves keep drifting"}

    return {
        "as_of": datetime.now(ET).isoformat(),
        "pead_shock_drift": pead_stats,
        "walk_forward_top20": wf,
        "decay": {"rank_autocorr_21d": round(float(np.mean(rank_autocorr)), 3)
                  if rank_autocorr else None,
                  "top_decile_retention_21d": round(float(np.mean(decile_retention)), 3)
                  if decile_retention else None,
                  "note": "high = sticky ranks → daily sheet is fresh enough; "
                          "low = need intra-month refresh"},
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
    d = v.get("decay") or {}
    if d.get("rank_autocorr_21d") is not None:
        L.append(f"decay: rank autocorr(21d)={d['rank_autocorr_21d']}  "
                 f"top-decile retention={d['top_decile_retention_21d']}")
    ps = v.get("pead_shock_drift")
    if ps:
        L.append(f"PEAD shock drift: {ps['mean_signed_drift_excess_21d_pct']:+.2f}%/21d "
                 f"(t={ps['t_stat']}, n={ps['n_periods']}) — "
                 + ("shock sheet has signal" if (ps['t_stat'] or 0) > 1.5
                    else "shock sheet is candidates-only, NOT a standalone signal"))
    wf = v.get("walk_forward_top20")
    if wf:
        L.append(f"WALK-FORWARD top-20: {wf['total_return_pct']:+.1f}% vs SPY "
                 f"{wf['spy_total_pct']:+.1f}% over {wf['n_periods']} periods · "
                 f"excess {wf['mean_excess_per_21d_pct']:+.2f}%/21d (t={wf['excess_t_stat']}) · "
                 f"beta {wf['beta_vs_spy']} → alpha {wf['alpha_per_21d_pct']:+.2f}%/21d · "
                 f"beat SPY {wf['pct_periods_beat_spy']:.0f}% · "
                 f"worst {wf['worst_period_pct']:+.1f}%")
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
