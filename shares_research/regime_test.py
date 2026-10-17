"""Multi-regime durability test — the real "is there an edge" gate.

A signal that's only positive in a 2024-26 bull is regime-luck, not edge. This
runs every signal's market-neutral L/S Sharpe across distinct regimes (calendar
years + the 2020 crash + 2022 bear) and flags the ones that are *consistently*
positive — the only kind worth trading and worth paying for clean data to chase.

Runs on the long-history panels (shares_research/data/panels_long), which still
carry survivorship bias (today's names) — so momentum/trend numbers stay
inflated; durability ACROSS regimes is what we trust, not the level.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from shares_research.harness import fwd_return, load_panels, signals

LONG = Path("shares_research/data/panels_long")

# Distinct regimes. Sub-windows (crash/bear) overlap the year buckets on purpose
# so we can see how a signal behaves specifically in stress.
REGIMES = {
    "2018":        ("2018-01-01", "2018-12-31"),  # vol spike + Q4 selloff
    "2019":        ("2019-01-01", "2019-12-31"),  # bull
    "2020_crash":  ("2020-02-15", "2020-04-15"),  # COVID crash
    "2020_rip":    ("2020-04-16", "2020-12-31"),  # recovery rip
    "2021":        ("2021-01-01", "2021-12-31"),  # bull/melt-up
    "2022_bear":   ("2022-01-01", "2022-12-31"),  # bear
    "2023":        ("2023-01-01", "2023-12-31"),  # recovery
    "2024_26":     ("2024-01-01", "2026-12-31"),  # bull
}


def ls_sharpe(sig: pd.DataFrame, close: pd.DataFrame, liquid: pd.DataFrame,
              dates: pd.DatetimeIndex, h: int = 5, decile: float = 0.1,
              cost: float = 0.0012):
    """Market-neutral top-minus-bottom-decile Sharpe over the given dates."""
    fwd = fwd_return(close, h)
    s = sig.where(liquid)
    ls = []
    for d in dates:
        a, f = s.loc[d], fwd.loc[d]
        ok = a.notna() & f.notna()
        if ok.sum() < 80:
            continue
        hi, lo = a[ok].quantile(1 - decile), a[ok].quantile(decile)
        ls.append(f[ok][a[ok] >= hi].mean() - f[ok][a[ok] <= lo].mean())
    ls = pd.Series(ls)
    if len(ls) < 8 or ls.std() == 0:
        return None
    return ls.mean() / ls.std() * np.sqrt(252 / h)


def main(h: int = 5):
    P = load_panels(path=LONG)
    close, liquid = P["close"], P["liquid"]
    print(f"long panels: {close.shape}  {close.index.min().date()} → {close.index.max().date()}"
          f"  liquid/day ~{int(liquid.sum(axis=1).median())}")
    sigs = signals(P)

    regimes = {k: v for k, v in REGIMES.items()
               if pd.Timestamp(v[0]) >= close.index.min() - pd.Timedelta(days=400)}

    print(f"\n{'signal':12}" + "".join(f"{k.replace('_',''):>9}" for k in regimes)
          + f"{'minS':>7}{'%pos':>6}  durable?")
    for nm, s in sigs.items():
        cells, sharpes = [], []
        for k, (a, b) in regimes.items():
            mask = (close.index >= a) & (close.index <= b)
            dts = close.index[mask]  # lookbacks warm from pre-regime panel data;
            # ls_sharpe skips dates with too few valid (non-NaN) names automatically
            sh = ls_sharpe(s, close, liquid, dts, h=h)
            sharpes.append(sh)
            cells.append(f"{sh:>+9.1f}" if sh is not None else f"{'·':>9}")
        valid = [x for x in sharpes if x is not None]
        if not valid:
            continue
        mn = min(valid)
        pos = sum(x > 0 for x in valid) / len(valid)
        durable = "YES" if (mn > 0.3 and pos >= 0.8) else ("mixed" if pos >= 0.6 else "no")
        print(f"{nm:12}" + "".join(cells) + f"{mn:>+7.1f}{pos*100:>5.0f}%  {durable}")

    print("\n  durable = positive in ≥80% of regimes AND worst-regime Sharpe > 0.3.")
    print("  (Sharpe per regime, market-neutral L/S, 5d horizon, net ~0.24% round-trip.)")


if __name__ == "__main__":
    main()
