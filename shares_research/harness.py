"""Rigorous, cost-aware single-name equity signal validation.

Given any signal panel (dates x tickers), reports:
  - IC  : daily cross-sectional rank corr vs forward return, with a t-stat.
  - L/S : market-neutral top-decile minus bottom-decile spread (strips beta —
          the decisive "alpha vs bull-market-beta" test), annualised + Sharpe.
  - PT/SL: a realistic path backtest — enter top-decile at close, exit on the
          first day high>=PT or low<=SL, else time-stop. Uses high/low so the
          intraday touch is real, not assumed.

HONESTY (do not paper over):
  * Universe = TODAY's liquid S&P names → SURVIVORSHIP BIAS. It inflates
    long/momentum signals. Short-horizon + market-neutral results are less
    affected; long-only momentum results should be discounted.
  * Window is a single 2024-26 BULL regime — no bear / momentum-crash in sample.
  * Costs are modelled but optimistic (0.12% round-trip). Real turnover/slippage
    is worse; treat thin edges as probably-zero after frictions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PANELS = Path("advisor/data/research/panels")
DEFAULT_COST = 0.0012  # ~0.12% round-trip (liquid names) — optimistic


def load_panels(path: Path = PANELS, min_dollar_vol: float = 20e6) -> dict:
    """Load close/high/low/volume + a liquidity mask (20d median $vol).
    `path` lets callers point at a different panel set (e.g. the long-history
    panels under shares_research/data/panels_long)."""
    path = Path(path)
    p = {}
    for nm in ("close", "high", "low", "volume"):
        p[nm] = pd.read_parquet(path / f"{nm}.parquet").sort_index().astype(float)
    dvol = (p["close"] * p["volume"]).rolling(20).median()
    p["liquid"] = dvol > min_dollar_vol
    p["ret1"] = p["close"].pct_change(fill_method=None)
    return p


def fwd_return(close: pd.DataFrame, h: int) -> pd.DataFrame:
    return close.shift(-h) / close - 1


def _ic_series(sig: pd.DataFrame, fwd: pd.DataFrame, liquid: pd.DataFrame,
               min_names: int = 50) -> pd.Series:
    s = sig.where(liquid)
    f = fwd.where(liquid)
    out = {}
    for d in s.index:
        a, b = s.loc[d], f.loc[d]
        ok = a.notna() & b.notna()
        if ok.sum() >= min_names:
            out[d] = a[ok].rank().corr(b[ok].rank())
    return pd.Series(out).dropna()


def evaluate(signal: pd.DataFrame, panels: dict, horizons=(1, 5, 10),
             decile: float = 0.1, cost: float = DEFAULT_COST,
             warmup: int = 260) -> pd.DataFrame:
    """IC + market-neutral L/S per horizon. Returns one row per horizon."""
    close, liquid = panels["close"], panels["liquid"]
    sig = signal.where(liquid)
    end = -(max(horizons) + 1)
    idx = close.index[warmup:end]
    rows = []
    for h in horizons:
        fwd = fwd_return(close, h)
        ics = _ic_series(sig.loc[idx], fwd.loc[idx], liquid.loc[idx])
        ic_m = ics.mean()
        ic_t = ic_m / ics.std() * np.sqrt(len(ics)) if ics.std() > 0 else 0.0
        ls = []
        for d in idx:
            a, f = sig.loc[d], fwd.loc[d]
            ok = a.notna() & f.notna()
            if ok.sum() < 100:
                continue
            hi, lo = a[ok].quantile(1 - decile), a[ok].quantile(decile)
            ls.append(f[ok][a[ok] >= hi].mean() - f[ok][a[ok] <= lo].mean())
        ls = pd.Series(ls)
        ann = (1 + ls.mean() - 2 * cost) ** (252 / h) - 1 if len(ls) else np.nan
        shp = ls.mean() / ls.std() * np.sqrt(252 / h) if ls.std() > 0 else 0.0
        rows.append(dict(h=h, ic=round(ic_m, 4), ic_t=round(ic_t, 1),
                         ls_per=round(ls.mean(), 4),
                         ls_ann_net=round(ann, 3) if ann == ann else None,
                         ls_sharpe=round(shp, 2),
                         pos_days=round((ls > 0).mean(), 2)))
    return pd.DataFrame(rows)


def ptsl_backtest(signal: pd.DataFrame, panels: dict, pt: float = 0.05,
                  sl: float = 0.05, hold: int = 5, decile: float = 0.1,
                  cost: float = DEFAULT_COST, warmup: int = 260,
                  side: str = "long") -> dict:
    """Realistic PT/SL path sim on the top-decile (side='long') or bottom-decile
    (side='short') of the signal. Per-trade expectancy is the meaningful number;
    `total` is a sum of overlapping trades, NOT a compounded equity curve."""
    close, high, low, liquid = (panels["close"], panels["high"],
                                panels["low"], panels["liquid"])
    sig = signal.where(liquid)
    idx = close.index[warmup:-(hold + 1)]
    pnl, wins, losses, tos = [], 0, 0, 0
    short = side == "short"
    for d in idx:
        a = sig.loc[d]
        ok = a.notna()
        if ok.sum() < 100:
            continue
        thr = a[ok].quantile(decile if short else 1 - decile)
        names = a[ok][a[ok] <= thr].index if short else a[ok][a[ok] >= thr].index
        pos = close.index.get_loc(d)
        ent = close.loc[d, names]
        for nm in names:
            e = ent[nm]
            if not np.isfinite(e):
                continue
            done = False
            for hh in range(1, hold + 1):
                hd = close.index[pos + hh]
                hi, lo = high.loc[hd, nm], low.loc[hd, nm]
                # For a long: SL on low<=, PT on high>=. For a short: mirrored.
                stop_hit = (lo <= e * (1 - sl)) if not short else (hi >= e * (1 + sl))
                tgt_hit = (hi >= e * (1 + pt)) if not short else (lo <= e * (1 - pt))
                stop_px = lo if not short else hi
                tgt_px = hi if not short else lo
                if np.isfinite(stop_px) and stop_hit:
                    pnl.append(-sl - cost); losses += 1; done = True; break
                if np.isfinite(tgt_px) and tgt_hit:
                    pnl.append(pt - cost); wins += 1; done = True; break
            if not done:
                cl = close.iloc[pos + hold][nm]
                r = (cl / e - 1) if not short else (e / cl - 1)
                pnl.append(r - cost); tos += 1
    pnl = np.array(pnl)
    n = len(pnl)
    return dict(side=side, pt=pt, sl=sl, hold=hold, trades=n,
                win=round(wins / n, 3) if n else 0,
                timeout=tos, exp_per_trade=round(float(pnl.mean()), 5) if n else 0,
                total=round(float(pnl.sum()), 3) if n else 0)


# ── Signal library — each returns a (dates x tickers) panel ──────────────────
def signals(p: dict) -> dict:
    c, h, l, v, r = p["close"], p["high"], p["low"], p["volume"], p["ret1"]
    return {
        "rev_1d":      -r,
        "rev_5d":      -(c / c.shift(5) - 1),
        "mom_12_1":    c.shift(21) / c.shift(252) - 1,
        "dist_50ma":   -(c / c.rolling(50).mean() - 1),   # below-MA reversion
        "dist_200ma":  -(c / c.rolling(200).mean() - 1),
        "above_50ma":  c / c.rolling(50).mean() - 1,       # trend
        "lo_52w_prox": -(c / c.rolling(252).min()),        # near 52w low = oversold
        "rvol_20d":    r.rolling(20).std(),
        "volspike":    v / v.rolling(20).mean(),
        "drawdown_20": c / c.rolling(20).max() - 1,        # depth below recent peak
    }


if __name__ == "__main__":
    P = load_panels()
    print(f"panels: close {P['close'].shape}  liquid names/day "
          f"~{int(P['liquid'].sum(axis=1).median())}")
    sigs = signals(P)
    print("\n=== L/S (market-neutral) summary, fwd 5d ===")
    print(f"{'signal':12}{'IC':>8}{'IC_t':>7}{'LS/5d':>9}{'annNet':>9}{'Sharpe':>8}{'%pos':>7}")
    for nm, s in sigs.items():
        df = evaluate(s, P, horizons=(5,))
        row = df.iloc[0]
        print(f"{nm:12}{row.ic:>+8.4f}{row.ic_t:>+7.1f}{row.ls_per:>+9.4f}"
              f"{(row.ls_ann_net or 0)*100:>+8.0f}%{row.ls_sharpe:>+8.2f}{row.pos_days*100:>6.0f}%")
