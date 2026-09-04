"""Realized factor-return series → volatility scaling and crash-state gating.

WHY
---
The engine's measured failure was not a bad ranking function. It was that
`detect_regime` reads MARKET regime — VIX, credit spreads, the 200-day — all
of which stayed benign through Jul-Aug 2026 while the FACTOR regime turned.
Momentum kept its regime weight while its realized IC ran -0.19 to -0.26.

The literature does not solve this with a better on/off switch:

  Barroso & Santa-Clara (2015), "Momentum has its moments" (JFE) — scaling
  momentum exposure by its own trailing realized volatility substantially
  raises risk-adjusted return and largely removes the crash tail.

  Daniel & Moskowitz (2016), "Momentum Crashes" (JFE) — momentum's payoff is
  option-like conditional on (a) a bear market state and (b) elevated
  volatility. The crashes are forecastable in advance from those two states.

So: measure each factor's own realized return series, scale weights by
inverse volatility, and shrink harder when a factor sits in the
Daniel-Moskowitz crash state.

HOW THE SERIES IS BUILT
-----------------------
`raw_factors()` anchors every window to the LAST row of the panel it is
handed (`.iloc[-1]`, `.iloc[-21]`, ...). So a historical cross-section is
just the same production function called on a truncated panel — no parallel
re-implementation that can drift from what actually ships.

At each sample date we form the long-short decile spread on each factor's
sector-neutral z-score, hold it `STEP` trading days, and record the return.
That is a factor return, not an IC: it is what the factor would have paid.

DISCIPLINE (matches ic_weight_multipliers)
  * Multipliers are DE-WEIGHT ONLY, capped at 1.0 — no factor is ever
    amplified on thin evidence. Only relative order matters downstream, so
    relative scaling delivers the whole effect.
  * Normalized against the MEDIAN factor volatility, not the minimum. With a
    min anchor, one very-low-vol factor (turn_anom runs ~6% annualized vs
    ~26% for the rest) drags every other multiplier onto the floor, which
    reads as "only the weakest factor counts" — the opposite of risk parity.
  * A floor stops any factor being switched off entirely.
  * Below MIN_OBS the whole layer is inert and reports why.

MEASURED 2026-09-04 (240 daily observations):
    factor       vol63   volAll   trail126   crash    mult
    mom_12_1     0.335    0.261     -0.036    yes    0.464
    rev_1m       0.288    0.212     +0.078     no    0.899
    prox_52w     0.210    0.205     -0.159     no    1.000
    lowvol       0.256    0.218     -0.120    yes    0.600
    turn_anom    0.062    0.066     -0.031     no    1.000
    resid_mom    0.263    0.218     -0.024    yes    0.592
Momentum is independently flagged as elevated-vol AND in drawdown — the
condition that produced the -0.19 to -0.26 realized ICs.

CLI: python -m advisor.research.factor_returns [--rebuild] [--step 5]
Writes advisor/data/research/factor_returns.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "factor_returns.json"

# STEP=1 gives DAILY factor returns. The z-scores on consecutive days are of
# course autocorrelated, but the RETURNS are strictly non-overlapping (t->t+1,
# then t+1->t+2), which is what a volatility estimate needs. A 2y panel minus
# the 252d mom_12_1 warm-up leaves ~237 usable days at step 1 versus ~48 at
# step 5 — the difference between a usable vol estimate and a guess.
STEP = 1                 # trading days held per observation (daily)
DECILE = 0.10            # long top decile, short bottom decile
MIN_OBS = 90             # daily obs required before the layer does anything
VOL_WINDOW = 63          # ~3 months for the current volatility estimate
CRASH_LOOKBACK = 126     # ~6 months for the Daniel-Moskowitz bear-state test
FLOOR = 0.25             # never switch a factor off
CRASH_SHRINK = 0.60      # extra multiplier while in the DM crash state
CRASH_VOL_RATIO = 1.10   # vol must be 10% above baseline to count as elevated

FACTORS = ("mom_12_1", "rev_1m", "prox_52w", "lowvol", "turn_anom", "resid_mom")


def _spread_return(z_col, fwd_ret) -> float | None:
    """Equal-weight top-decile minus bottom-decile forward return."""
    x = z_col.dropna()
    common = x.index.intersection(fwd_ret.dropna().index)
    if len(common) < 100:
        return None
    x = x.loc[common]
    n = max(int(len(x) * DECILE), 10)
    ranked = x.sort_values()
    short_leg = fwd_ret.loc[ranked.index[:n]].mean()
    long_leg = fwd_ret.loc[ranked.index[-n:]].mean()
    if long_leg != long_leg or short_leg != short_leg:
        return None
    return float(long_leg - short_leg)


def build(step: int = STEP, max_samples: int | None = None,
          verbose: bool = True) -> dict:
    """Replay the production factor construction across history."""
    import numpy as np
    import pandas as pd
    from advisor.research.datastore import load_panel
    from advisor.research.factors import raw_factors
    from advisor.research.universe import load as load_universe

    u = load_universe()
    close, volume = load_panel("close"), load_panel("volume")
    rets = close.pct_change()
    dates = list(close.index)

    # need 252d of history for mom_12_1 plus `step` forward days to score
    start = 260
    idxs = list(range(start, len(dates) - step, step))
    if max_samples:
        idxs = idxs[-max_samples:]

    obs = []
    for k, i in enumerate(idxs):
        try:
            c, v, r = close.iloc[:i + 1], volume.iloc[:i + 1], rets.iloc[:i + 1]
            _f, z, _liq, _dv, _px, _sm, _sd = raw_factors(c, v, r, u)
            fwd = close.iloc[i + step] / close.iloc[i] - 1
            row = {"date": str(dates[i].date()),
                   "fwd_end": str(dates[i + step].date()), "ret": {}}
            for col in FACTORS:
                if col not in z:
                    continue
                sr = _spread_return(z[col], fwd)
                if sr is not None:
                    row["ret"][col] = round(sr, 6)
            if row["ret"]:
                obs.append(row)
        except Exception as exc:
            if verbose:
                print(f"[factor_returns] {dates[i].date()}: {exc}", file=sys.stderr)
        if verbose and (k + 1) % 20 == 0:
            print(f"[factor_returns] {k + 1}/{len(idxs)} samples", flush=True)

    payload = {
        "schema_version": 1,
        "built_at": datetime.now(ET).isoformat(),
        "step_td": step, "decile": DECILE, "n_obs": len(obs),
        "method": (f"long top {DECILE:.0%} minus bottom {DECILE:.0%} of each "
                   f"factor's sector-neutral z, held {step} trading days; "
                   "cross-sections replayed through the production "
                   "raw_factors() on truncated panels"),
        "series": obs,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, OUT)
    if verbose:
        print(f"[factor_returns] {len(obs)} observations -> {OUT.name}")
    return payload


def _load() -> dict:
    try:
        return json.loads(OUT.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def stats() -> dict:
    """Per-factor annualized vol, trailing return, and crash state."""
    import numpy as np

    doc = _load()
    series = doc.get("series") or []
    step = int(doc.get("step_td") or STEP)
    per_year = 252 / step
    out: dict[str, dict] = {}
    for col in FACTORS:
        vals = [r["ret"][col] for r in series if col in (r.get("ret") or {})]
        if len(vals) < MIN_OBS:
            out[col] = {"n": len(vals), "usable": False}
            continue
        arr = np.array(vals, dtype=float)
        recent = arr[-VOL_WINDOW:]
        vol = float(np.std(recent, ddof=1) * np.sqrt(per_year))
        full_vol = float(np.std(arr, ddof=1) * np.sqrt(per_year))
        trail = float(np.prod(1 + arr[-CRASH_LOOKBACK:]) - 1)
        # Daniel-Moskowitz: bear state AND MEANINGFULLY elevated volatility.
        # `vol > full_vol` alone fires about half the time by construction, so
        # it needs a margin to discriminate rather than just split the sample.
        crash = bool(trail < 0 and vol > CRASH_VOL_RATIO * full_vol)
        out[col] = {"n": len(vals), "usable": True,
                    "vol_ann": round(vol, 4),
                    "vol_ann_full": round(full_vol, 4),
                    "trailing_return": round(trail, 4),
                    "crash_state": crash}
    return out


def vol_weight_multipliers() -> dict:
    """Inverse-volatility multipliers, de-weight only.

    Risk parity expressed RELATIVELY: the lowest-volatility usable factor
    gets 1.0, every other factor is scaled to match its risk contribution.
    Nothing is amplified — only relative order matters downstream, so
    relative scaling delivers the whole effect without ever handing a factor
    more weight than the regime allotted it.
    """
    st = stats()
    usable = {k: v for k, v in st.items() if v.get("usable")}
    if len(usable) < 2:
        return {"enabled": False,
                "reason": (f"need >={MIN_OBS} observations on >=2 factors; "
                           f"have {[(k, v['n']) for k, v in st.items()]}"),
                "multipliers": {}, "detail": st}
    # Normalize to the MEDIAN volatility, not the minimum. With a min anchor a
    # single very-low-vol factor (turn_anom sits at ~6% vs ~26% for the rest)
    # drags every other multiplier onto the floor, which reads as "only the
    # weakest factor counts" — the opposite of risk parity. Median keeps the
    # typical factor near 1.0 and scales the genuinely volatile ones down.
    vols = sorted(v["vol_ann"] for v in usable.values() if v["vol_ann"] > 0)
    base = vols[len(vols) // 2] if len(vols) % 2 else (
        (vols[len(vols) // 2 - 1] + vols[len(vols) // 2]) / 2)
    mults, detail = {}, {}
    for k, v in st.items():
        if not v.get("usable") or not v["vol_ann"]:
            mults[k] = 1.0
            detail[k] = {**v, "mult": 1.0, "reason": "insufficient history"}
            continue
        m = min(1.0, base / v["vol_ann"])
        if v.get("crash_state"):
            m *= CRASH_SHRINK
        m = round(max(FLOOR, m), 4)
        mults[k] = m
        detail[k] = {**v, "mult": m,
                     "reason": ("crash state (bear + elevated vol)"
                                if v.get("crash_state") else "inverse-vol")}
    return {"enabled": True, "multipliers": mults, "detail": detail,
            "params": {"floor": FLOOR, "crash_shrink": CRASH_SHRINK,
                       "vol_window": VOL_WINDOW, "min_obs": MIN_OBS},
            "median_vol_ann": round(base, 4),
            "policy": ("inverse-volatility risk parity vs the MEDIAN factor "
                       "volatility, de-weight only (capped at 1.0); extra "
                       "shrink in the Daniel-Moskowitz crash state "
                       f"(bear + vol > {CRASH_VOL_RATIO}x baseline)"),
            "built_at": _load().get("built_at")}


def main() -> int:
    if "--rebuild" in sys.argv:
        step = int(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else STEP
        n = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else None
        build(step=step, max_samples=n)
    m = vol_weight_multipliers()
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
