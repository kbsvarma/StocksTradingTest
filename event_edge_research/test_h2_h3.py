"""H2 (turn-of-month) and H3 (overnight vs intraday) — pre-registered tests.
Runs on the ET session layer (sessions.py). Reports IS and OOS separately, net of
MES friction, with HAC t-stats and drop-worst-year robustness.
"""
import numpy as np, pandas as pd
from sessions import build_sessions
from stats_tools import (split_is_oos, nw_tstat, worst_year_drop_tstat,
                         summarize_dollar, fmt, MES_MULT, MES_FRICTION_RT,
                         MNQ_MULT, MNQ_FRICTION_RT)

def dollars_per_micro(ret, price, mult, friction):
    """Net $ P&L of a 1-micro long capturing `ret` on an instrument priced `price`."""
    return ret * price * mult - friction

def run_h3(tag, mult, friction):
    print(f"\n{'#'*72}\n# H3 OVERNIGHT vs INTRADAY — {tag}\n{'#'*72}")
    s = build_sessions(tag)
    # exclude roll-spanning overnights (can't hold through a contract change cleanly)
    on = s[~s["overnight_roll"]].copy()
    # Net $ per 1 micro, long the whole window
    on["on_$"] = dollars_per_micro(on["overnight_ret"], on["prev_close"], mult, friction)
    s["id_$"] = dollars_per_micro(s["intraday_ret"], s["rth_open"], mult, friction)

    for name, series in [("OVERNIGHT long", on["on_$"]), ("INTRADAY long", s["id_$"])]:
        is_p, oos_p = split_is_oos(series)
        print(f"\n{name}:")
        print("  IS  " + fmt(summarize_dollar(is_p, "2010-2020")))
        print("  OOS " + fmt(summarize_dollar(oos_p, "2021-2026")))
        t_is, _ = nw_tstat(is_p.dropna().values)
        tw, wy, wm = worst_year_drop_tstat(is_p.dropna())
        print(f"  IS drop-worst-year ({wy}, mean=${wm:+.2f}): t={tw:+.2f}")

def run_h2(tag, mult, friction):
    print(f"\n{'#'*72}\n# H2 TURN-OF-MONTH — {tag}\n{'#'*72}")
    s = build_sessions(tag).copy()
    s["cc_$"] = dollars_per_micro(s["cc_ret"], s["prev_close"], mult, friction=0.0)  # gross per day; friction added per-trade below
    # Define turn-of-month window: last trading day of month (T-1 relative to month roll)
    # through +3 trading days. Flag each session's position in the month.
    idx = s.index
    s["ym"] = idx.to_period("M")
    s["dom_rank_fromend"] = s.groupby("ym").cumcount(ascending=False)  # 0 = last trading day of month
    # next month's first trading days
    s["month_pos"] = np.arange(len(s))
    # Build TOM flag: last trading day of month (rank_fromend==0) and first 3 of next month
    is_last = s["dom_rank_fromend"] == 0
    first_rank = s.groupby("ym").cumcount()  # 0 = first trading day of month
    is_first3 = first_rank <= 2
    s["tom"] = is_last | is_first3
    # gross close-to-close daily $ (per micro) already in cc_$
    tom_daily = s.loc[s["tom"], "cc_$"]
    rest_daily = s.loc[~s["tom"], "cc_$"]

    for name, series in [("TOM days (in-window)", tom_daily), ("non-TOM days", rest_daily)]:
        is_p, oos_p = split_is_oos(series)
        print(f"\n{name} [gross close-to-close $/day per micro]:")
        print("  IS  " + fmt(summarize_dollar(is_p, "2010-2020")))
        print("  OOS " + fmt(summarize_dollar(oos_p, "2021-2026")))

    # Tradable proxy: one position entered at close of T-2 (day before last), held to
    # close of T+3 -> spans up to 5 sessions, ONE round trip friction.
    # Approximate net by summing the in-window daily cc returns and charging one RT.
    s["tom_grp"] = (s["tom"] != s["tom"].shift()).cumsum()
    trades = []
    for grp, block in s[s["tom"]].groupby("tom_grp"):
        if len(block) < 2:
            continue
        gross = block["cc_$"].sum()
        trades.append((block.index[-1], gross - friction))
    tr = pd.Series({d: v for d, v in trades}).sort_index()
    is_p, oos_p = split_is_oos(tr)
    print(f"\nTradable TOM proxy (1 micro, hold window, 1 RT friction):")
    print("  IS  " + fmt(summarize_dollar(is_p, "2010-2020")))
    print("  OOS " + fmt(summarize_dollar(oos_p, "2021-2026")))
    tw, wy, wm = worst_year_drop_tstat(is_p.dropna())
    print(f"  IS drop-worst-year ({wy}, mean=${wm:+.2f}): t={tw:+.2f}")

if __name__ == "__main__":
    for tag, mult, fr in [("ES", MES_MULT, MES_FRICTION_RT), ("NQ", MNQ_MULT, MNQ_FRICTION_RT)]:
        run_h3(tag, mult, fr)
    for tag, mult, fr in [("ES", MES_MULT, MES_FRICTION_RT), ("NQ", MNQ_MULT, MNQ_FRICTION_RT)]:
        run_h2(tag, mult, fr)
