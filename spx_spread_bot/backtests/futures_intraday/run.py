"""Run the intraday strategy battery across markets, net of costs.
Reports daily-income metrics per ONE micro contract."""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from engine import MARKETS, load_rth, _cost_pts, _day_metrics, orb, vwap_revert, gap_fade, last_hour_mom

DDIR = Path(__file__).parent

def run_strategy(sessions, mkt, fn, needs_prev=False, **kw):
    cost = _cost_pts(mkt)
    dates = sorted(sessions.keys())
    daily = {}
    prev_close = None
    for d in dates:
        day = sessions[d]
        if needs_prev:
            pts, n = fn(day, prev_close, **kw)
        else:
            pts, n = fn(day, **kw)
        prev_close = day["close"].iloc[-1]
        net_pts = pts - n * cost
        daily[d] = net_pts * mkt.point_value
    return pd.Series(daily)

def main():
    variants = [
        ("ORB-5",   orb, False, dict(or_min=5)),
        ("ORB-15",  orb, False, dict(or_min=15)),
        ("ORB-30",  orb, False, dict(or_min=30)),
        ("ORB-60",  orb, False, dict(or_min=60)),
        ("VWAPrev-0.5%", vwap_revert, False, dict(ext=0.5)),
        ("VWAPrev-1.0%", vwap_revert, False, dict(ext=1.0)),
        ("GapFade",  gap_fade, True, {}),
        ("LastHrMom", last_hour_mom, False, {}),
    ]
    for code, mkt in MARKETS.items():
        path = DDIR / f"{code}_1m.parquet"
        if not path.exists():
            print(f"[skip] {code}: no data"); continue
        sessions = load_rth(str(path), mkt)
        print(f"\n{'='*92}\n{mkt.name}  ({len(sessions)} trading days, net of cost ~{_cost_pts(mkt):.3f} pts/RT)\n{'='*92}")
        import copy
        m0 = copy.copy(mkt); m0.slip_ticks = 0.0; m0.commission_rt = 0.0
        print(f"{'Strategy':<14}{'days':>6}{'GROSS$/d':>10}{'NET$/d':>9}{'net%pos':>9}"
              f"{'worst$':>9}{'netSharpe':>10}{'maxDD$':>9}{'PF':>6}{'netTotal$':>11}")
        for name, fn, needs_prev, kw in variants:
            net = run_strategy(sessions, mkt, fn, needs_prev, **kw)
            gross = run_strategy(sessions, m0, fn, needs_prev, **kw)
            m = _day_metrics(net)
            if not m:
                print(f"{name:<14}  (insufficient)"); continue
            print(f"{name:<14}{m['days']:>6}{gross.mean():>+10.2f}{m['avg_$/day']:>+9.2f}{m['%pos']:>8.0f}%"
                  f"{m['worst']:>+9.0f}{m['daily_Sharpe']:>+10.2f}"
                  f"{m['maxDD_$']:>+9.0f}{m['profit_factor']:>6.2f}{m['total_$']:>+11.0f}")

if __name__ == "__main__":
    main()
