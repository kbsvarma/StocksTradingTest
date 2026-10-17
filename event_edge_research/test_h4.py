"""H4 — Conditional overnight reversal (Nagel 2012 liquidity-provision premium).
Tune the conditioning rule on IS (2010-2020) ONLY; single OOS shot on 2021-2026.
RV regime computed in-data (trailing 20-session std of intraday_ret). No external data.
"""
import numpy as np, pandas as pd
from sessions import build_sessions
from stats_tools import (split_is_oos, nw_tstat, worst_year_drop_tstat,
                         summarize_dollar, fmt, MES_MULT, MES_FRICTION_RT,
                         MNQ_MULT, MNQ_FRICTION_RT)

def prep(tag):
    s = build_sessions(tag)
    s = s[~s["overnight_roll"]].copy()          # clean overnight windows
    s["rv20"] = s["intraday_ret"].rolling(20).std()
    s["prev_intraday"] = s["intraday_ret"].shift(1)   # yesterday's intraday move
    s["prev_rv20"] = s["rv20"].shift(1)               # regime known at entry (close[d])
    return s

def cell_pnl(s, mult, friction, thr, hi_vol_only):
    """Overnight-long P&L on nights following prev_intraday < thr (and optionally
    high-RV regime). Entry price = prev_close (the close we buy at)."""
    cond = s["prev_intraday"] < thr
    if hi_vol_only:
        med = s["prev_rv20"].median()
        cond = cond & (s["prev_rv20"] > med)
    sel = s[cond]
    pnl = sel["overnight_ret"] * sel["prev_close"] * mult - friction
    return pnl

def unconditional(s, mult, friction):
    return s["overnight_ret"] * s["prev_close"] * mult - friction

def main():
    grid_thr = [0.0, -0.005, -0.010]
    grid_vol = [False, True]
    for tag, mult, fr in [("ES", MES_MULT, MES_FRICTION_RT), ("NQ", MNQ_MULT, MNQ_FRICTION_RT)]:
        print("\n" + "#"*74 + f"\n# H4 conditional overnight — {tag}\n" + "#"*74)
        s = prep(tag)
        uncond = unconditional(s, mult, fr)
        u_is, _ = split_is_oos(uncond)
        print("  UNCOND overnight IS baseline: " + fmt(summarize_dollar(u_is, "all-nights IS")))

        # --- tune on IS only ---
        print("\n  IS grid (tuning — OOS NOT looked at):")
        best = None
        for thr in grid_thr:
            for hv in grid_vol:
                pnl = cell_pnl(s, mult, fr, thr, hv)
                is_p, _ = split_is_oos(pnl)
                d = summarize_dollar(is_p, f"thr={thr:+.3f} hiVol={hv}")
                t = d["t"]
                print("    " + fmt(d))
                if d["n"] >= 40 and (best is None or (t if not np.isnan(t) else -9) > best[0]):
                    best = (t, thr, hv)
        print(f"\n  >>> IS-best cell: thr={best[1]:+.3f} hiVol={best[2]} (IS t={best[0]:+.2f})")

        # drop-worst-year on the chosen IS cell
        pnl_best = cell_pnl(s, mult, fr, best[1], best[2])
        is_best, oos_best = split_is_oos(pnl_best)
        tw, wy, wm = worst_year_drop_tstat(is_best.dropna(), lags=3)
        print(f"      IS drop-worst-year ({wy}, mean=${wm:+.2f}): t={tw:+.2f}")

        # --- single OOS shot on the locked cell ---
        print(f"\n  *** OOS single shot (locked cell) ***")
        print("      " + fmt(summarize_dollar(oos_best, "2021-2026 OOS")))

if __name__ == "__main__":
    main()
