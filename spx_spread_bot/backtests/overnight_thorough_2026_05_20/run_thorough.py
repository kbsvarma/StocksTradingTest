"""
Thorough overnight-options analysis. Builds on overnight_short_premium_2026_05_20.

Adds:
  1. Long-premium variants (puts/calls/straddle/strangle) — confirm/refute user hypothesis
  2. SL variants for short_put_DTE3_OTM50bp using ES-proxy SL fire detection
  3. Tail-hedged short put (buy further-OTM put as wing)
  4. VIX-regime breakdown of winner
  5. Seasonality (month, FOMC weeks, OPEX weeks, quarter-end, holiday-shortened)
  6. Day-of-week breakdown (including Fri->Mon weekend hold)
  7. Entry/exit time sensitivity — limited (we only have daily OHLC)
  8. Size scaling — analytical (slippage scales)

Reuses load_data.load_all() and run_overnight helpers.
"""
from __future__ import annotations
import sys, os, json, warnings
from dataclasses import asdict
from datetime import date, timedelta, datetime
from pathlib import Path
import numpy as np
import pandas as pd

# import the existing infra
SIBLING = Path(__file__).resolve().parent.parent / "overnight_short_premium_2026_05_20"
sys.path.insert(0, str(SIBLING))
from load_data import load_all  # noqa: E402
from run_overnight import (  # noqa: E402
    load_spx_daily, pick_strike_by_otm, pick_strike_below,
    find_contract_next_day, expiry_for_dte, simulate_single_leg, stats, Trade,
)

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def trades_to_df(trades):
    return pd.DataFrame([asdict(t) for t in trades])


def summarize_df(df, pnl_col="pnl_real"):
    if df.empty:
        return dict(n=0)
    pnls = df[pnl_col].values
    credits = df["credit_close"].values
    n = len(df)
    total = float(pnls.sum())
    avg = float(pnls.mean())
    std = float(pnls.std()) if n > 1 else 0.0
    sharpe = float(avg / std * np.sqrt(252)) if std > 0 else 0.0
    win_rate = float((pnls > 0).mean() * 100)
    avg_win = float(pnls[pnls > 0].mean()) if (pnls > 0).any() else 0.0
    avg_loss = float(pnls[pnls <= 0].mean()) if (pnls <= 0).any() else 0.0
    max_loss = float(pnls.min())
    max_win = float(pnls.max())
    blowups_1k = int((pnls < -1000).sum())
    blowups_5k = int((pnls < -5000).sum())
    first = pd.to_datetime(df["entry_date"].min())
    last = pd.to_datetime(df["entry_date"].max())
    yrs = (last - first).days / 365.25 if last > first else 0
    return dict(
        n=n, win_rate=win_rate, total=total, avg=avg, std=std, sharpe=sharpe,
        avg_win=avg_win, avg_loss=avg_loss, max_loss=max_loss, max_win=max_win,
        blowups_gt_1k=blowups_1k, blowups_gt_5k=blowups_5k,
        avg_credit=float(credits.mean()) if n else 0,
        years=yrs, pnl_per_yr=float(total / yrs) if yrs > 0 else 0,
    )


# ---------------------------------------------------------------------------
# 1. LONG-PREMIUM variants (buy at close, sell at open)
# ---------------------------------------------------------------------------

def simulate_long_leg(raw, spx, dte, otm_pct, side):
    """Buy single put/call at day-N close, sell at day-N+1 open.
    P&L per contract = (open_N+1 - close_N) * 100 - slippage_both_legs.
    """
    trades = []
    days = sorted(raw.keys())
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        opts = day_obj.get(side, {}).get(expiry)
        if opts is None or opts.empty: continue
        pick = pick_strike_by_otm(opts, spx_close, otm_pct, side)
        if pick is None: continue
        strike = float(pick["strike"])
        debit_close = float(pick["close"])  # cost to buy
        if debit_close <= 0.05: continue
        nxt = find_contract_next_day(raw, next_day, side, expiry, strike)
        if nxt is None: continue
        credit_open = float(nxt["open"])  # sale proceeds
        if credit_open < 0: continue
        # Long: profit = sell - buy
        pnl_no_slip = (credit_open - debit_close) * 100.0
        # Realistic: slip 0.20 buy (lift offer at close) + 0.20 sell (hit bid at open) — but we model "fair mark";
        # to be conservative apply 0.20 each side = 0.40 total
        pnl_real = (credit_open - debit_close - 0.40) * 100.0
        pnl_opt = (credit_open - debit_close - 0.10) * 100.0
        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant=f"long_{'put' if side=='P' else 'call'}",
            dte_at_sell=dte, otm_pct=otm_pct,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=strike, long_strike=float("nan"), right=side,
            credit_close=debit_close*100,  # repurposed: debit paid
            debit_open=credit_open*100,    # repurposed: proceeds
            n_legs=1, pnl_opt=pnl_no_slip, pnl_real=pnl_real,
            pnl_optimistic=pnl_opt, vix=vix_val,
        ))
    return trades


def simulate_long_straddle_strangle(raw, spx, dte, otm_pct, name):
    trades = []
    days = sorted(raw.keys())
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        puts = day_obj.get("P", {}).get(expiry); calls = day_obj.get("C", {}).get(expiry)
        if puts is None or calls is None: continue
        p = pick_strike_by_otm(puts, spx_close, otm_pct, "P")
        c = pick_strike_by_otm(calls, spx_close, otm_pct, "C")
        if p is None or c is None: continue
        p_strike = float(p["strike"]); c_strike = float(c["strike"])
        p_close = float(p["close"]); c_close = float(c["close"])
        debit_close = p_close + c_close
        if debit_close <= 0.10: continue
        p_nxt = find_contract_next_day(raw, next_day, "P", expiry, p_strike)
        c_nxt = find_contract_next_day(raw, next_day, "C", expiry, c_strike)
        if p_nxt is None or c_nxt is None: continue
        credit_open = float(p_nxt["open"]) + float(c_nxt["open"])
        pnl_no_slip = (credit_open - debit_close) * 100.0
        pnl_real = (credit_open - debit_close - 0.80) * 100.0  # 2 legs both sides
        pnl_opt = (credit_open - debit_close - 0.20) * 100.0
        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant=name, dte_at_sell=dte, otm_pct=otm_pct,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=p_strike, long_strike=c_strike, right="PC",
            credit_close=debit_close*100, debit_open=credit_open*100,
            n_legs=2, pnl_opt=pnl_no_slip, pnl_real=pnl_real,
            pnl_optimistic=pnl_opt, vix=vix_val,
        ))
    return trades


# ---------------------------------------------------------------------------
# 2. SHORT-PUT WITH SL variants for short_put_DTE3_OTM50bp
# ---------------------------------------------------------------------------
# We don't have intraday options data, so use SPX overnight low (proxy for ES low)
# as the proxy for "did the option move enough to trigger SL during the night".
#
# Approach: estimate option price at the day-N+1 low. We have day-N+1 OHLC for
# the option contract — use the "high" (peak premium) as conservative proxy for
# worst overnight mark. If high * 100 >= sl_multiple * credit, we mark SL fired
# and close at the high price (worst-realistic).

def simulate_short_put_with_sl(raw, spx, dte=3, otm_pct=0.005, sl_mult=None,
                                sl_pct_maxloss=None, hard_close_early=False):
    """SL variants for short put.

    OVERNIGHT-ONLY SL DETECTION: We cannot use the next-day option HIGH because
    that includes intraday post-open movement (irrelevant to an overnight trade
    that closes at open). Instead, we use SPX's overnight gap (next-day open vs
    prior close) as the proxy. If the gap is sufficiently adverse that the
    option open price already reflects a >= sl_mult * credit mark, we say "SL
    would have fired" — but the realized fill is just the open mark anyway, so
    no actionable difference for overnight-only positions.

    For a TRUE overnight SL we'd need ES futures intra-night data we don't have.
    So this is a "what fraction of overnight blowups COULD have been caught" model.
    We trigger when debit_open >= sl_mult * credit_close (SL would fire AT open).
    Close price = max(open, trigger) + extra panicky slip.
    """
    trades = []
    days = sorted(raw.keys())
    side = "P"
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        opts = day_obj.get(side, {}).get(expiry)
        if opts is None or opts.empty: continue
        short = pick_strike_by_otm(opts, spx_close, otm_pct, side)
        if short is None: continue
        strike = float(short["strike"])
        credit_close = float(short["close"])
        if credit_close <= 0.02: continue
        nxt = find_contract_next_day(raw, next_day, side, expiry, strike)
        if nxt is None: continue
        debit_open = float(nxt["open"])
        # SL trigger uses ONLY the next-day OPEN price (overnight realized adverse move).
        # We do not use nxt["high"] because that includes post-open intraday movement
        # which is irrelevant to an overnight strategy that closes at open.
        sl_fired = False
        trigger = None
        if sl_mult is not None:
            trigger = sl_mult * credit_close
            if debit_open >= trigger:
                sl_fired = True
        elif sl_pct_maxloss is not None:
            max_loss_per_contract = (strike - credit_close)
            trigger = credit_close + sl_pct_maxloss * max_loss_per_contract
            if debit_open >= trigger:
                sl_fired = True

        if sl_fired:
            # Open is already past the trigger — fill at open with extra slippage
            close_price = debit_open
            pnl_opt_v = (credit_close - close_price) * 100.0
            pnl_real_v = (credit_close - close_price - 0.30) * 100.0
            pnl_optim_v = (credit_close - close_price - 0.10) * 100.0
        elif hard_close_early:
            # Premature close: assume we exit at approximate midpoint between
            # day-N close and day-N+1 open (representing exit before market open).
            # Proxy: weighted average favoring close (theta works overnight).
            close_price = 0.6 * debit_open + 0.4 * credit_close
            pnl_opt_v = (credit_close - close_price) * 100.0
            pnl_real_v = (credit_close - close_price - 0.20) * 100.0
            pnl_optim_v = (credit_close - close_price - 0.05) * 100.0
        else:
            pnl_opt_v = (credit_close - debit_open) * 100.0
            pnl_real_v = (credit_close - debit_open - 0.20) * 100.0
            pnl_optim_v = (credit_close - debit_open - 0.05) * 100.0

        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant="short_put_sl",
            dte_at_sell=dte, otm_pct=otm_pct,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=strike, long_strike=float("nan"), right=side,
            credit_close=credit_close*100, debit_open=debit_open*100,
            n_legs=1, pnl_opt=pnl_opt_v, pnl_real=pnl_real_v,
            pnl_optimistic=pnl_optim_v, vix=vix_val,
        ))
    return trades


# ---------------------------------------------------------------------------
# 3. TAIL-HEDGE: short_put_DTE3_OTM50bp + long put further OTM (wing)
# ---------------------------------------------------------------------------

def simulate_tail_hedge(raw, spx, dte=3, short_otm=0.005, wing_otm=0.02):
    """Short put OTM50bp + long put at wing_otm further out."""
    trades = []
    days = sorted(raw.keys())
    side = "P"
    for i, day in enumerate(days[:-1]):
        next_day = days[i + 1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        row = spx.loc[ts]; row1 = spx.loc[ts1]
        spx_close = float(row["spx_close"]); spx_open1 = float(row1["spx_open"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        expiry = expiry_for_dte(day, dte)
        if expiry < next_day: continue
        day_obj = raw.get(day, {})
        puts = day_obj.get("P", {}).get(expiry)
        if puts is None: continue
        s = pick_strike_by_otm(puts, spx_close, short_otm, side)
        l = pick_strike_by_otm(puts, spx_close, wing_otm, side)
        if s is None or l is None: continue
        s_strike = float(s["strike"]); l_strike = float(l["strike"])
        if l_strike >= s_strike: continue  # wing must be below short
        s_close = float(s["close"]); l_close = float(l["close"])
        credit_close = s_close - l_close
        if credit_close <= 0.05: continue
        s_nxt = find_contract_next_day(raw, next_day, "P", expiry, s_strike)
        l_nxt = find_contract_next_day(raw, next_day, "P", expiry, l_strike)
        if s_nxt is None or l_nxt is None: continue
        debit_open = float(s_nxt["open"]) - float(l_nxt["open"])
        pnl_opt = (credit_close - debit_open) * 100.0
        pnl_real = (credit_close - debit_open - 0.40) * 100.0
        pnl_optimistic = (credit_close - debit_open - 0.10) * 100.0
        trades.append(Trade(
            entry_date=day.isoformat(), next_date=next_day.isoformat(),
            variant=f"tail_hedge_wing{int(wing_otm*10000)}bp",
            dte_at_sell=dte, otm_pct=short_otm,
            spx_close_N=spx_close, spx_open_N1=spx_open1,
            overnight_pct=(spx_open1/spx_close - 1)*100,
            short_strike=s_strike, long_strike=l_strike, right="BPS",
            credit_close=credit_close*100, debit_open=debit_open*100,
            n_legs=2, pnl_opt=pnl_opt, pnl_real=pnl_real,
            pnl_optimistic=pnl_optimistic, vix=vix_val,
        ))
    return trades


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

# FOMC meeting dates (approx — 2014-2026). Source: hand-compiled major dates.
FOMC_DATES = [
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18", "2014-07-30", "2014-09-17", "2014-10-29", "2014-12-17",
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29",
]
FOMC = set(pd.to_datetime(FOMC_DATES).date)


def is_opex_week(d: date) -> bool:
    # OPEX = 3rd Friday of month; "OPEX week" = Mon-Fri of that week.
    # Find 3rd Friday of d's month.
    first = date(d.year, d.month, 1)
    # day-of-week: Friday=4
    days_to_first_fri = (4 - first.weekday()) % 7
    third_fri = first + timedelta(days=days_to_first_fri + 14)
    return third_fri - timedelta(days=4) <= d <= third_fri


def is_fomc_window(d: date) -> bool:
    # FOMC day or day before
    return d in FOMC or (d + timedelta(days=1)) in FOMC


def is_quarter_end_week(d: date) -> bool:
    # last 5 trading-day window of mar, jun, sep, dec
    if d.month not in (3, 6, 9, 12): return False
    last_day = date(d.year, d.month + 1, 1) - timedelta(days=1) if d.month < 12 else date(d.year, 12, 31)
    return (last_day - d).days <= 7


def is_holiday_short_week(d: date) -> bool:
    # crude proxy: weeks containing US market holidays (NYSE)
    holidays_2014_2026 = [
        # MLK, Presidents, Good Friday, Memorial, Juneteenth, July4, Labor, Thanksgiving, Christmas, NewYear
    ]
    # Build dynamically: we'll mark first week of Jan, week of July 4, week of Thanksgiving, week of Christmas
    if d.month == 1 and d.day <= 7: return True
    if d.month == 7 and 1 <= d.day <= 7: return True
    if d.month == 11 and 22 <= d.day <= 28: return True
    if d.month == 12 and d.day >= 22: return True
    return False


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def main():
    print("Loading data...")
    raw = load_all()
    spx = load_spx_daily()
    days = sorted(raw.keys())
    print(f"Period: {days[0]} -> {days[-1]} ({len(days)} days)")

    summary = {}

    # =====================================================================
    # 1. LONG-PREMIUM VARIANTS
    # =====================================================================
    print("\n========== 1. LONG-PREMIUM VARIANTS ==========")
    long_results = []
    for side in ["P", "C"]:
        for dte in [1, 2, 3]:
            for otm in [0.0025, 0.0050, 0.0100, 0.0200]:
                label = f"long_{'put' if side=='P' else 'call'}_DTE{dte}_OTM{int(otm*10000)}bp"
                trades = simulate_long_leg(raw, spx, dte, otm, side)
                if not trades:
                    print(f"  {label}: no trades"); continue
                df = trades_to_df(trades)
                s = summarize_df(df, "pnl_real")
                long_results.append({"variant": label, **s})
                print(f"  {label}: n={s['n']} wr={s['win_rate']:.1f}% "
                      f"total=${s['total']:,.0f} ({s['pnl_per_yr']:+.0f}/yr) "
                      f"sharpe={s['sharpe']:.2f} maxL=${s['max_loss']:,.0f} maxW=${s['max_win']:,.0f}")
                df.to_csv(OUT / f"trades_{label}.csv", index=False)

    for dte in [1, 2, 3]:
        # long straddle
        label = f"long_straddle_DTE{dte}_ATM"
        trades = simulate_long_straddle_strangle(raw, spx, dte, 0.0, label)
        if trades:
            df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
            long_results.append({"variant": label, **s})
            print(f"  {label}: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} "
                  f"({s['pnl_per_yr']:+.0f}/yr) sharpe={s['sharpe']:.2f} maxL=${s['max_loss']:,.0f}")
            df.to_csv(OUT / f"trades_{label}.csv", index=False)
        # long strangle
        label = f"long_strangle_DTE{dte}_OTM50bp"
        trades = simulate_long_straddle_strangle(raw, spx, dte, 0.005, label)
        if trades:
            df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
            long_results.append({"variant": label, **s})
            print(f"  {label}: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} "
                  f"({s['pnl_per_yr']:+.0f}/yr) sharpe={s['sharpe']:.2f} maxL=${s['max_loss']:,.0f}")
            df.to_csv(OUT / f"trades_{label}.csv", index=False)

    pd.DataFrame(long_results).to_csv(OUT / "long_premium_leaderboard.csv", index=False)
    summary["long_premium"] = long_results

    # =====================================================================
    # 2. SL VARIANTS on winner
    # =====================================================================
    print("\n========== 2. SL VARIANTS — short_put_DTE3_OTM50bp ==========")
    sl_results = []
    # Baseline (no SL)
    trades = simulate_short_put_with_sl(raw, spx, 3, 0.005, sl_mult=None)
    df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
    sl_results.append({"variant": "baseline_noSL", **s, "sl_fired_pct": 0.0})
    print(f"  baseline_noSL: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} maxL=${s['max_loss']:,.0f}")

    for mult in [2.5, 5.0, 10.0]:
        trades = simulate_short_put_with_sl(raw, spx, 3, 0.005, sl_mult=mult)
        df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
        # SL fired = pnl below approx -1.5*credit (rough indicator)
        fired_pct = float((df["pnl_real"] < -(mult-1) * df["credit_close"]).mean() * 100)
        sl_results.append({"variant": f"SL_{mult}x_credit", **s, "sl_fired_pct": fired_pct})
        print(f"  SL_{mult}x: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} "
              f"maxL=${s['max_loss']:,.0f} blowups>5k={s['blowups_gt_5k']}")
        df.to_csv(OUT / f"trades_SL_{mult}x.csv", index=False)

    for pct in [0.25, 0.50]:
        trades = simulate_short_put_with_sl(raw, spx, 3, 0.005, sl_pct_maxloss=pct)
        df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
        sl_results.append({"variant": f"SL_{int(pct*100)}pct_maxloss", **s, "sl_fired_pct": float("nan")})
        print(f"  SL_{int(pct*100)}pct_maxloss: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} "
              f"maxL=${s['max_loss']:,.0f}")
        df.to_csv(OUT / f"trades_SL_{int(pct*100)}pct.csv", index=False)

    # Premature close
    trades = simulate_short_put_with_sl(raw, spx, 3, 0.005, hard_close_early=True)
    df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
    sl_results.append({"variant": "premature_close_1hr", **s, "sl_fired_pct": float("nan")})
    print(f"  premature_close_1hr: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f}")

    pd.DataFrame(sl_results).to_csv(OUT / "sl_leaderboard.csv", index=False)
    summary["sl_variants"] = sl_results

    # =====================================================================
    # 3. TAIL HEDGE
    # =====================================================================
    print("\n========== 3. TAIL HEDGE ==========")
    th_results = []
    for wing in [0.02, 0.03, 0.05]:
        label = f"tail_hedge_short50bp_wing{int(wing*10000)}bp"
        trades = simulate_tail_hedge(raw, spx, 3, 0.005, wing)
        if not trades:
            print(f"  {label}: no trades"); continue
        df = trades_to_df(trades); s = summarize_df(df, "pnl_real")
        th_results.append({"variant": label, **s})
        print(f"  {label}: n={s['n']} wr={s['win_rate']:.1f}% total=${s['total']:,.0f} "
              f"({s['pnl_per_yr']:+.0f}/yr) maxL=${s['max_loss']:,.0f} avg_credit=${s['avg_credit']:.0f}")
        df.to_csv(OUT / f"trades_{label}.csv", index=False)
    pd.DataFrame(th_results).to_csv(OUT / "tail_hedge_leaderboard.csv", index=False)
    summary["tail_hedge"] = th_results

    # =====================================================================
    # 4. VIX REGIME BREAKDOWN — load existing winner CSV
    # =====================================================================
    print("\n========== 4. VIX REGIME BREAKDOWN — winner ==========")
    winner_csv = SIBLING / "trades_short_put_DTE3_OTM50bp.csv"
    wdf = pd.read_csv(winner_csv)
    wdf["entry_date"] = pd.to_datetime(wdf["entry_date"])
    bins = [0, 15, 20, 25, 100]
    labels = ["<15", "15-20", "20-25", ">25"]
    wdf["vix_bin"] = pd.cut(wdf["vix"], bins=bins, labels=labels)
    vix_table = wdf.groupby("vix_bin").agg(
        n=("pnl_real", "size"),
        total=("pnl_real", "sum"),
        avg=("pnl_real", "mean"),
        std=("pnl_real", "std"),
        win_rate=("pnl_real", lambda x: (x > 0).mean() * 100),
        max_loss=("pnl_real", "min"),
        max_win=("pnl_real", "max"),
        blowups_1k=("pnl_real", lambda x: (x < -1000).sum()),
        blowups_5k=("pnl_real", lambda x: (x < -5000).sum()),
    ).round(2)
    print(vix_table.to_string())
    vix_table.to_csv(OUT / "vix_regime_breakdown.csv")
    summary["vix_regime"] = vix_table.reset_index().to_dict("records")

    # =====================================================================
    # 5. SEASONALITY
    # =====================================================================
    print("\n========== 5. SEASONALITY — winner ==========")
    wdf["month"] = wdf["entry_date"].dt.month
    month_table = wdf.groupby("month").agg(
        n=("pnl_real", "size"),
        total=("pnl_real", "sum"),
        avg=("pnl_real", "mean"),
        win_rate=("pnl_real", lambda x: (x > 0).mean() * 100),
        max_loss=("pnl_real", "min"),
    ).round(2)
    print("\nBy Month:"); print(month_table.to_string())
    month_table.to_csv(OUT / "seasonality_month.csv")

    wdf["is_fomc"] = wdf["entry_date"].dt.date.apply(is_fomc_window)
    wdf["is_opex"] = wdf["entry_date"].dt.date.apply(is_opex_week)
    wdf["is_qend"] = wdf["entry_date"].dt.date.apply(is_quarter_end_week)
    wdf["is_holshort"] = wdf["entry_date"].dt.date.apply(is_holiday_short_week)
    flag_tables = {}
    for col in ["is_fomc", "is_opex", "is_qend", "is_holshort"]:
        t = wdf.groupby(col).agg(
            n=("pnl_real", "size"),
            total=("pnl_real", "sum"),
            avg=("pnl_real", "mean"),
            win_rate=("pnl_real", lambda x: (x > 0).mean() * 100),
            max_loss=("pnl_real", "min"),
        ).round(2)
        print(f"\n{col}:"); print(t.to_string())
        flag_tables[col] = t.reset_index().to_dict("records")
        t.to_csv(OUT / f"seasonality_{col}.csv")
    summary["seasonality"] = {"month": month_table.reset_index().to_dict("records"), **flag_tables}

    # =====================================================================
    # 6. DAY-OF-WEEK
    # =====================================================================
    print("\n========== 6. DAY-OF-WEEK ==========")
    wdf["dow"] = wdf["entry_date"].dt.day_name()
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    dow_table = wdf.groupby("dow").agg(
        n=("pnl_real", "size"),
        total=("pnl_real", "sum"),
        avg=("pnl_real", "mean"),
        win_rate=("pnl_real", lambda x: (x > 0).mean() * 100),
        max_loss=("pnl_real", "min"),
    ).round(2).reindex(dow_order)
    print(dow_table.to_string())
    dow_table.to_csv(OUT / "day_of_week.csv")
    summary["day_of_week"] = dow_table.reset_index().to_dict("records")

    # =====================================================================
    # 7. ENTRY-TIME SENSITIVITY (limited — only daily OHLC)
    # =====================================================================
    # Approximation: we can't truly simulate 3:55pm vs 4:00pm. But we can
    # report: how much of the edge comes from the overnight period vs intraday.
    # We do this by computing: if we instead exited at the next-day HIGH (worst
    # for short) and LOW (best for short) of the option, what would total P&L be?
    print("\n========== 7. ENTRY-TIME SENSITIVITY (proxy) ==========")
    # Re-derive by directly accessing raw data — replicate winner trades with
    # access to next-day high/low of the option.
    print("  Building best/worst-fill envelope...")
    rows = []
    for i, day in enumerate(days[:-1]):
        next_day = days[i+1]
        ts = pd.Timestamp(day); ts1 = pd.Timestamp(next_day)
        if ts not in spx.index or ts1 not in spx.index: continue
        spx_close = float(spx.loc[ts, "spx_close"])
        expiry = expiry_for_dte(day, 3)
        if expiry < next_day: continue
        opts = raw.get(day, {}).get("P", {}).get(expiry)
        if opts is None or opts.empty: continue
        short = pick_strike_by_otm(opts, spx_close, 0.005, "P")
        if short is None: continue
        strike = float(short["strike"]); cc = float(short["close"])
        if cc <= 0.02: continue
        nxt = find_contract_next_day(raw, next_day, "P", expiry, strike)
        if nxt is None: continue
        rows.append({
            "entry_date": day.isoformat(),
            "credit_close": cc,
            "open": float(nxt["open"]),
            "high": float(nxt["high"]),
            "low":  float(nxt["low"]),
            "close": float(nxt["close"]),
        })
    fillenv = pd.DataFrame(rows)
    fillenv["pnl_open"]  = (fillenv["credit_close"] - fillenv["open"]  - 0.20) * 100
    fillenv["pnl_low"]   = (fillenv["credit_close"] - fillenv["low"]   - 0.20) * 100
    fillenv["pnl_high"]  = (fillenv["credit_close"] - fillenv["high"]  - 0.20) * 100
    fillenv["pnl_close"] = (fillenv["credit_close"] - fillenv["close"] - 0.20) * 100
    env_summary = {
        "best_fill_total":  float(fillenv["pnl_low"].sum()),
        "open_fill_total":  float(fillenv["pnl_open"].sum()),
        "close_fill_total": float(fillenv["pnl_close"].sum()),
        "worst_fill_total": float(fillenv["pnl_high"].sum()),
        "n": len(fillenv),
    }
    for k, v in env_summary.items():
        print(f"  {k}: {v if isinstance(v,int) else f'${v:,.0f}'}")
    fillenv.to_csv(OUT / "entry_time_envelope.csv", index=False)
    summary["entry_time_envelope"] = env_summary

    # =====================================================================
    # 8. SIZE SCALING — analytical
    # =====================================================================
    print("\n========== 8. SIZE SCALING ==========")
    # Slippage in run_overnight is per-leg per-side flat $0.20. In reality SPX
    # bid-ask widens with size, especially overnight. Model:
    # extra_slip_per_contract = 0.05 * max(0, log10(n)) — fractional widening
    scaling = []
    base_pnls = wdf["pnl_real"].values
    base_credits = wdf["credit_close"].values
    for n_contracts in [1, 2, 5, 10, 20]:
        # Per-contract slippage penalty grows with size
        extra_slip_pc = 0.05 * np.log10(max(n_contracts, 1)) * 100  # dollars/contract
        # Total P&L if we'd run n_contracts each night
        total = (base_pnls.sum() - extra_slip_pc * len(base_pnls)) * n_contracts
        per_contract_total = total / n_contracts
        scaling.append({
            "n_contracts": n_contracts,
            "extra_slip_per_contract_dollars": round(extra_slip_pc, 1),
            "total_pnl_position": round(total, 0),
            "per_contract_total": round(per_contract_total, 0),
            "per_contract_pnl_per_yr": round(per_contract_total / 12.3, 0),
        })
        print(f"  n={n_contracts}: extra_slip=${extra_slip_pc:.0f}/contract "
              f"total=${total:,.0f}  per-contract=${per_contract_total:,.0f}")
    pd.DataFrame(scaling).to_csv(OUT / "size_scaling.csv", index=False)
    summary["size_scaling"] = scaling

    # =====================================================================
    # Save full summary
    # =====================================================================
    with (OUT / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n\nDone — outputs in {OUT}")


if __name__ == "__main__":
    main()
