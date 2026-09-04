"""Transaction-cost model — what a pick would actually cost to trade.

WHY
---
There was no cost model anywhere in this codebase. For a research product
that is survivable; for anything claiming to be decision-grade it is not,
because cost is the most common way a documented anomaly dies.

  Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading
  Costs" (RFS) — a large share of published anomalies do not survive
  realistic trading costs, and survival is concentrated in low-turnover
  strategies. Cost sensitivity scales with turnover, so a 10-name daily
  slate on a 21-day horizon is precisely the profile that needs checking.

WHY NOT A BAR-BASED SPREAD ESTIMATOR
------------------------------------
The obvious move is Corwin & Schultz (2012) (JF) or Abdi & Ranaldo (2017)
(RFS), both of which recover spreads from daily high/low/close. Both are
implemented below as `corwin_schultz` / `abdi_ranaldo`, and BOTH FAIL A
SANITY CHECK on this panel (measured 2026-09-04):

    estimator          SPY     AAPL     MSFT      JPM       KO
    Corwin-Schultz    22.9     65.3     39.7     50.8     28.2   bps
    Abdi-Ranaldo       0.0      0.0      0.0     31.2     46.7   bps
    reality            <1       1-2      1-2      1-2      1-2   bps

They are off by one to two orders of magnitude, and inconsistently so. Both
were validated against eras and segments with far wider spreads; in modern
decimalized large-cap tape they attribute intraday volatility to spread.
Shipping either as an absolute cost would be worse than shipping nothing,
because it would look quantitative while being wrong.

WHAT THIS USES INSTEAD
----------------------
1. Spread from a LIQUIDITY-ANCHORED TIER, not a measurement: bps ≈
   31.6 * ADV($M)^-0.5, scaled by volatility relative to the universe median.
   The inverse-square-root form has real grounding — inventory and
   adverse-selection models both imply spread falls with volume — and the
   anchors are set to well-known orders of magnitude for US equities
   ($1B ADV ≈ 1bp, $100M ≈ 3bps, $10M ≈ 10bps, $1M ≈ 32bps). It is a TIER,
   labelled as such, and never claimed to be a measured spread.

2. Market impact via the square-root law: impact scales with volatility times
   the square root of participation. This one is well grounded empirically
   and depends only on sigma and ADV, both of which we have reliably. For any
   meaningful order size it dominates the spread term anyway.

The bar estimators are retained and reported as `diagnostic_*` so the
disagreement stays visible rather than being quietly dropped.

WHAT IT IS NOT
--------------
An ESTIMATE, not measured execution. Labelled modelled, never realized.
A pick's cost figure is a screen, not a fill.

CLI: python -m advisor.research.costs [TICKER ...]
"""
from __future__ import annotations

import sys

# square-root impact law: impact = IMPACT_COEF * sigma_daily * sqrt(participation)
IMPACT_COEF = 0.8
PARTICIPATION = 0.01     # assume the order is 1% of 21d median dollar volume
SPREAD_WINDOW = 60       # trading days of high/low pairs for the diagnostics

# Liquidity-anchored spread tier: bps = SPREAD_A * ADV($M) ** -SPREAD_B.
# Anchors: $1B ADV -> ~1bp, $100M -> ~3bps, $10M -> ~10bps, $1M -> ~32bps.
SPREAD_A, SPREAD_B = 31.6, 0.5
VOL_TILT = 0.5           # exponent on (sigma / median sigma)
MIN_SPREAD_BPS = 0.5
MAX_SPREAD_BPS = 300.0

# A pick is uneconomic when its target move is small relative to what a
# round trip costs. 3x is deliberately lenient — it screens the obviously
# broken, it does not certify the rest.
MIN_TARGET_TO_COST = 3.0


def corwin_schultz(high, low, window: int = SPREAD_WINDOW):
    """Effective spread as a FRACTION of price, per ticker (wide panels in).

    Returns a Series indexed by ticker. Negative two-day estimates are set to
    zero before averaging, as the authors prescribe — they arise from
    estimation noise, not from negative spreads.
    """
    import numpy as np
    import pandas as pd

    h, l = high.tail(window + 1), low.tail(window + 1)
    if len(h) < 10:
        return pd.Series(dtype=float)
    # single-day log range squared
    with np.errstate(divide="ignore", invalid="ignore"):
        beta_1 = np.log(h / l) ** 2
    beta = beta_1 + beta_1.shift(1)                       # two consecutive days
    # two-day high and low
    h2 = pd.concat([h, h.shift(1)]).groupby(level=0).max()
    l2 = pd.concat([l, l.shift(1)]).groupby(level=0).min()
    h2, l2 = h2.reindex(h.index), l2.reindex(l.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        gamma = np.log(h2 / l2) ** 2

    k = 3 - 2 * (2 ** 0.5)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    spread = spread.where(spread > 0, 0.0)                # per Corwin-Schultz
    return spread.replace([np.inf, -np.inf], np.nan).mean()


def abdi_ranaldo(high, low, close, window: int = SPREAD_WINDOW):
    """Abdi & Ranaldo (2017) CHL estimator — DIAGNOSTIC ONLY, see module docs."""
    import numpy as np

    h, l, c = high.tail(window), low.tail(window), close.tail(window)
    lc, eta = np.log(c), (np.log(h) + np.log(l)) / 2.0
    prod = (lc - eta) * (lc - eta.shift(-1))
    return np.sqrt((4.0 * prod.mean()).clip(lower=0))


def estimate(tickers=None, participation: float = PARTICIPATION) -> dict:
    """Round-trip cost in basis points per ticker, with its components."""
    import numpy as np
    from advisor.research.datastore import load_panel

    close, high, low = load_panel("close"), load_panel("high"), load_panel("low")
    volume = load_panel("volume")
    # sigma and ADV medians must come from the FULL universe, not from the
    # handful of tickers asked about — otherwise the tilt is relative to a
    # sample of ten names and means nothing.
    rets_all = close.pct_change()
    sigma_all = rets_all.tail(63).std()
    sigma_med = float(sigma_all.median())

    if tickers is not None:
        keep = [t for t in tickers if t in close.columns]
        close, high, low, volume = (close[keep], high[keep], low[keep],
                                    volume[keep])

    sigma_daily = close.pct_change().tail(63).std()
    dollar_vol = (close * volume).rolling(21, min_periods=10).median().iloc[-1]
    adv_musd = (dollar_vol / 1e6).clip(lower=0.05)

    tilt = (sigma_daily / (sigma_med or 1.0)).clip(0.25, 4.0) ** VOL_TILT
    spread_bps = (SPREAD_A * adv_musd ** (-SPREAD_B) * tilt).clip(
        MIN_SPREAD_BPS, MAX_SPREAD_BPS)
    impact_bps = IMPACT_COEF * sigma_daily * (participation ** 0.5) * 1e4
    round_trip = spread_bps + 2.0 * impact_bps

    diag_cs = corwin_schultz(high, low) * 1e4
    diag_ar = abdi_ranaldo(high, low, close) * 1e4

    out = {}
    for t in close.columns:
        s, i, rt = spread_bps.get(t), impact_bps.get(t), round_trip.get(t)
        if not all(isinstance(x, (int, float)) and x == x for x in (s, i, rt)):
            continue
        out[t] = {
            "spread_bps": round(float(s), 1),
            "impact_bps": round(float(i), 1),
            "round_trip_bps": round(float(rt), 1),
            "adv_musd": round(float(adv_musd.get(t)), 1),
            "participation": participation,
            # kept visible so the disagreement is never hidden
            "diagnostic_corwin_schultz_bps": (round(float(diag_cs.get(t)), 1)
                                              if diag_cs.get(t) == diag_cs.get(t)
                                              else None),
            "diagnostic_abdi_ranaldo_bps": (round(float(diag_ar.get(t)), 1)
                                            if diag_ar.get(t) == diag_ar.get(t)
                                            else None),
            "method": (f"spread TIER {SPREAD_A}*ADV($M)^-{SPREAD_B} tilted by "
                       f"relative volatility + square-root impact at "
                       f"{participation:.1%} of 21d median dollar volume; "
                       "MODELLED, not a measured spread or a realized fill"),
        }
    return out


def screen(ticker: str, ref_px: float, target: float, cost: dict | None) -> dict:
    """Is the target move large enough to be worth trading?

    Returns the verdict plus every term, so a rejection is auditable rather
    than a silent drop.
    """
    if not cost:
        return {"ok": True, "verified": False, "reason": "no cost estimate available",
                "target_bps": None, "round_trip_bps": None, "ratio": None}
    try:
        target_bps = abs(target - ref_px) / ref_px * 1e4
    except (TypeError, ZeroDivisionError):
        return {"ok": False, "verified": False, "reason": "target/price unusable", "ratio": None}
    rt = cost.get("round_trip_bps") or 0.0
    import math
    if not isinstance(rt, (int, float)) or not math.isfinite(rt) or rt <= 0 \
            or not math.isfinite(target_bps):
        return {"ok": False, "verified": False,
                "reason": "invalid cost or price estimate", "ratio": None}
    ratio = round(target_bps / rt, 2) if rt else None
    ok = ratio is None or ratio >= MIN_TARGET_TO_COST
    return {
        "ok": bool(ok),
        "verified": True,
        "target_bps": round(target_bps, 1),
        "round_trip_bps": rt,
        "ratio": ratio,
        "min_ratio": MIN_TARGET_TO_COST,
        "reason": (None if ok else
                   f"target {target_bps:.0f}bps is only {ratio}x the modelled "
                   f"{rt:.0f}bps round trip (need {MIN_TARGET_TO_COST}x)"),
    }


def main() -> int:
    import json
    tickers = [a for a in sys.argv[1:] if not a.startswith("-")] or None
    est = estimate(tickers)
    rows = sorted(est.items(), key=lambda kv: -kv[1]["round_trip_bps"])
    print(f"{'TICKER':8} {'ADV$M':>8} {'SPREAD':>8} {'IMPACT':>8} "
          f"{'ROUNDTRIP':>10} {'[CS]':>7} {'[AR]':>7}")
    for t, c in rows[:40]:
        print(f"{t:8} {c['adv_musd']:8.1f} {c['spread_bps']:8.1f} "
              f"{c['impact_bps']:8.1f} {c['round_trip_bps']:10.1f} "
              f"{c['diagnostic_corwin_schultz_bps'] or 0:7.1f} "
              f"{c['diagnostic_abdi_ranaldo_bps'] or 0:7.1f}")
    print(f"\n{len(est)} tickers priced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
