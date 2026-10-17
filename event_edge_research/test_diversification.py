"""Does an overnight-index sleeve diversify the live SPX BPS bot?
Pre-registered gate (PREREGISTRATION_H4): daily-P&L correlation < 0.3 would justify
a marginal sleeve. Uses the BPS strategy's BACKTEST daily P&L (live config:
direction-filtered, 50pt, 2x SL) because the LIVE bot has only ~10 trades — too few
to estimate correlation. Overnight sleeve = long 1 micro close[d-1]->open[d], net.
"""
import numpy as np, pandas as pd
from pathlib import Path
from sessions import build_sessions
from stats_tools import MES_MULT, MNQ_MULT, MES_FRICTION_RT, MNQ_FRICTION_RT, ann_sharpe

ET = "America/New_York"
BPS = Path("../spx_spread_bot/backtests/direction_filter_ablation_2026_05_18/all_trades_sl20.csv")

def bps_daily():
    df = pd.read_csv(BPS, parse_dates=["date"])
    # live bot trades only when the direction filter passes
    traded = df[df["filter_passes"] == True].copy()
    s = traded.set_index("date")["net_pnl_$"].sort_index()
    # collapse any duplicate dates (shouldn't be, but be safe)
    s = s.groupby(s.index).sum()
    return s

def overnight_daily(tag, mult, friction):
    s = build_sessions(tag)
    s = s[~s["overnight_roll"]].copy()
    # overnight entered close[d-1] -> open[d]; realized ON date d (index is d already)
    pnl = s["overnight_ret"] * s["prev_close"] * mult - friction
    pnl.index = pnl.index.tz_localize(None).normalize()
    return pnl.dropna()

def analyze(tag, mult, fr):
    bps = bps_daily()
    on = overnight_daily(tag, mult, fr)
    # common calendar window
    lo = max(bps.index.min(), on.index.min())
    hi = min(bps.index.max(), on.index.max())
    print(f"\n{'='*70}\nOvernight sleeve = {tag}  |  common window {lo.date()} -> {hi.date()}\n{'='*70}")

    # (1) correlation on days BOTH trade (BPS filter-pass days that are also sessions)
    both = pd.concat([bps.rename("bps"), on.rename("on")], axis=1).dropna()
    both = both[(both.index >= lo) & (both.index <= hi)]
    r_both = both["bps"].corr(both["on"])
    print(f"days both trade: {len(both)}   corr(BPS, overnight) = {r_both:+.3f}")

    # (2) full-calendar daily P&L (0 when a strategy doesn't trade) over common window
    cal = pd.date_range(lo, hi, freq="B")
    bps_c = bps.reindex(cal).fillna(0.0)
    on_c = on.reindex(cal).fillna(0.0)
    r_cal = bps_c.corr(on_c)
    print(f"full calendar days: {len(cal)}   corr (0-filled) = {r_cal:+.3f}")

    # (3) combined-portfolio Sharpe at a few sleeve sizes (BPS=1 unit; N micros overnight)
    def sharpe(x): return ann_sharpe(x.values, periods_per_year=252)
    print(f"\n  BPS alone:        ann.Sharpe={sharpe(bps_c):+.2f}  $/yr={bps_c.sum()/((hi-lo).days/365.25):+8.0f}  std/day=${bps_c.std():.0f}")
    print(f"  Overnight alone:  ann.Sharpe={sharpe(on_c):+.2f}  $/yr={on_c.sum()/((hi-lo).days/365.25):+8.0f}  std/day=${on_c.std():.0f}")
    for n in (1, 3, 5):
        combo = bps_c + n * on_c
        print(f"  BPS + {n}x {tag} overnight:  ann.Sharpe={sharpe(combo):+.2f}  "
              f"$/yr={combo.sum()/((hi-lo).days/365.25):+8.0f}  std/day=${combo.std():.0f}")

if __name__ == "__main__":
    for tag, mult, fr in [("ES", MES_MULT, MES_FRICTION_RT), ("NQ", MNQ_MULT, MNQ_FRICTION_RT)]:
        analyze(tag, mult, fr)
