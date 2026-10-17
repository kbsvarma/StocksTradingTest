"""Covered-call WHEEL account simulator — plain dollars.

Models EXACTLY:
  1. Start with cash. Buy 100 shares.
  2. Each Monday: sell 1 weekly call at chosen strike, collect premium.
  3. Each Friday: if stock_close > strike -> assigned: sell 100 shares at strike.
                  if stock_close <= strike -> keep shares.
  4. Each Monday: if we hold 0 shares (got assigned), rebuy 100 shares at open.
  5. Collect dividends whenever we hold shares on an ex-dividend date.

Tracks total account value (cash + shares*price) week by week.
Compares against simple buy-and-hold of 100 shares (+ dividends).

Usage: python account_sim.py T --start 2023-01-01 --end 2025-12-31 --otm 1
"""
import re, sys, argparse
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

DATA = Path(__file__).parent / "data"
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SLIPPAGE = 0.03
GRIDS = {"XLF": 1.0, "SOFI": 0.5, "WFC": 1.0, "OXY": 1.0, "T": 0.5,
         "HPQ": 0.5, "MDT": 1.0, "VALE": 0.5, "PFE": 0.5}


def parse_sym(s):
    m = SYM_RE.match(s.strip())
    if not m: return None
    _, yymmdd, right, strike_str = m.groups()
    return (pd.Timestamp(f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}").date(),
            right, int(strike_str) / 1000)


def load_opt(ticker):
    df = pd.read_parquet(DATA / f"{ticker.lower()}_cc_daily.parquet").reset_index()
    df["date"] = pd.to_datetime(df["ts_event"]).dt.tz_localize(None).dt.date
    parsed = df["symbol"].apply(parse_sym)
    df = df[parsed.notna()].copy()
    df["expiry"] = [p[0] for p in parsed if p]
    df["right"] = [p[1] for p in parsed if p]
    df["strike"] = [p[2] for p in parsed if p]
    df = df[df["right"] == "C"]
    # volume-weighted close per (date, expiry, strike)
    g = df.groupby(["date", "expiry", "strike"])
    out = g.apply(lambda x: (x["close"] * x["volume"]).sum() / x["volume"].sum()
                  if x["volume"].sum() > 0 else x["close"].mean(),
                  include_groups=False).reset_index(name="close")
    idx = {}
    for _, r in out.iterrows():
        idx.setdefault((r["expiry"], r["date"]), {})[r["strike"]] = r["close"]
    return idx


def find_weeks(start, end):
    pairs = []
    d = pd.Timestamp(start)
    while d.weekday() != 0:
        d += pd.Timedelta(days=1)
    while d <= pd.Timestamp(end):
        fri = d + pd.Timedelta(days=4)
        if fri <= pd.Timestamp(end):
            pairs.append((d.date(), fri.date()))
        d += pd.Timedelta(days=7)
    return pairs


def nearest_on_after(stk_by_date, day, max_off=4):
    for off in range(max_off + 1):
        d = day + pd.Timedelta(days=off)
        if d in stk_by_date:
            return d
    return None


def nearest_on_before(stk_by_date, day, max_off=4):
    for off in range(max_off + 1):
        d = day - pd.Timedelta(days=off)
        if d in stk_by_date:
            return d
    return None


def simulate(ticker, otm_steps, start_str, end_str):
    grid = GRIDS.get(ticker, 1.0)
    opt_idx = load_opt(ticker)
    tk = yf.Ticker(ticker)
    stk = tk.history(start=start_str, end=end_str, auto_adjust=False)
    stk.index = pd.to_datetime(stk.index).tz_localize(None)
    stk_by_date = {d.date(): row for d, row in stk.iterrows()}
    # dividends
    divs = tk.dividends
    divs.index = pd.to_datetime(divs.index).tz_localize(None)
    div_by_date = {d.date(): float(v) for d, v in divs.items()
                   if pd.Timestamp(start_str) <= d <= pd.Timestamp(end_str)}

    weeks = find_weeks(start_str, end_str)
    if not weeks: return None

    # initial buy
    first_mon = nearest_on_after(stk_by_date, weeks[0][0])
    if first_mon is None: return None
    p0 = float(stk_by_date[first_mon]["Open"])
    start_capital = p0 * 100

    # CC account
    cash = 0.0
    shares = 100
    cc_buy_price = p0  # what we paid for current shares
    premium_collected = 0.0
    div_collected_cc = 0.0
    assignments = 0
    weeks_traded = 0
    # B&H account
    bh_shares = 100
    bh_div = 0.0

    log_rows = []

    for (mon, fri) in weeks:
        emon = nearest_on_after(stk_by_date, mon)
        efri = nearest_on_before(stk_by_date, fri)
        if emon is None or efri is None:
            continue

        # rebuy if we were assigned (hold 0 shares)
        if shares == 0:
            buy_px = float(stk_by_date[emon]["Open"])
            cash -= buy_px * 100
            shares = 100
            cc_buy_price = buy_px

        mon_open = float(stk_by_date[emon]["Open"])
        fri_close = float(stk_by_date[efri]["Close"])

        # choose strike around Monday open
        atm = round(mon_open / grid) * grid
        strike = atm + otm_steps * grid

        # premium for that strike on Monday
        prem = opt_idx.get((fri, emon), {}).get(strike)
        if prem is None or prem <= 0 or np.isnan(prem):
            # no option data this week -> just hold shares, no call sold
            pass
        else:
            premium = max(0.0, prem - SLIPPAGE)
            cash += premium * 100
            premium_collected += premium * 100
            weeks_traded += 1

        # dividends collected during the week (Mon..Fri) if holding shares
        for d, amt in div_by_date.items():
            if emon <= d <= efri:
                if shares > 0:
                    cash += amt * shares
                    div_collected_cc += amt * shares
                bh_div += amt * bh_shares

        # settle the call at Friday
        if prem is not None and prem > 0 and not np.isnan(prem):
            if fri_close > strike:
                # assigned: sell shares at strike
                cash += strike * 100
                shares = 0
                assignments += 1

        # record account value at Friday close
        cc_value = cash + shares * fri_close
        bh_value = bh_shares * fri_close + bh_div
        log_rows.append({
            "mon": emon, "fri": efri, "year": emon.year,
            "stock": fri_close, "strike": strike,
            "cc_value": cc_value, "bh_value": bh_value,
        })

    # final liquidation value
    last_fri = log_rows[-1]["fri"]
    last_px = log_rows[-1]["stock"]
    cc_final = cash + shares * last_px
    bh_final = bh_shares * last_px + bh_div

    return {
        "ticker": ticker, "otm_steps": otm_steps,
        "start_capital": start_capital,
        "cc_final": cc_final, "bh_final": bh_final,
        "premium_collected": premium_collected,
        "div_collected": div_collected_cc, "bh_div": bh_div,
        "assignments": assignments, "weeks_traded": weeks_traded,
        "n_weeks": len(log_rows),
        "years": (last_fri - log_rows[0]["mon"]).days / 365.25,
        "log": pd.DataFrame(log_rows),
    }


def report(r):
    if r is None:
        print("NO DATA"); return
    sc = r["start_capital"]
    cc_ret = (r["cc_final"] / sc - 1) * 100
    bh_ret = (r["bh_final"] / sc - 1) * 100
    cc_ann = cc_ret / r["years"]
    bh_ann = bh_ret / r["years"]
    lbl = {0: "ATM", 1: "+1 OTM", 2: "+2 OTM"}[r["otm_steps"]]
    print(f"\n{'='*78}")
    print(f"{r['ticker']}  {lbl}  —  {r['n_weeks']} weeks, {r['years']:.1f} yrs")
    print(f"{'='*78}")
    print(f"  Start capital (100 sh):     ${sc:>12,.0f}")
    print(f"  --- COVERED CALL WHEEL ---")
    print(f"  Premium collected:          ${r['premium_collected']:>12,.0f}")
    print(f"  Dividends collected:        ${r['div_collected']:>12,.0f}")
    print(f"  Assignments (called away):  {r['assignments']:>12} / {r['weeks_traded']} weeks")
    print(f"  Final account value:        ${r['cc_final']:>12,.0f}   ({cc_ret:+.1f}% total, {cc_ann:+.1f}%/yr)")
    print(f"  --- JUST HOLD 100 SHARES ---")
    print(f"  Dividends collected:        ${r['bh_div']:>12,.0f}")
    print(f"  Final account value:        ${r['bh_final']:>12,.0f}   ({bh_ret:+.1f}% total, {bh_ann:+.1f}%/yr)")
    print(f"  --- VERDICT ---")
    diff = r["cc_final"] - r["bh_final"]
    print(f"  CC wheel vs buy&hold:       ${diff:>+12,.0f}   ({'CC WINS' if diff>0 else 'buy&hold wins'})")

    # per-year account growth
    log = r["log"]
    print(f"  Year-end account value (CC vs B&H):")
    for yr in sorted(log["year"].unique()):
        s = log[log["year"] == yr]
        cc_e = s["cc_value"].iloc[-1]
        bh_e = s["bh_value"].iloc[-1]
        print(f"    {yr}:  CC ${cc_e:>10,.0f}   B&H ${bh_e:>10,.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--otm", type=int, default=None, help="OTM steps; default tests 0,1,2")
    args = ap.parse_args()

    steps = [args.otm] if args.otm is not None else [0, 1, 2]
    for s in steps:
        report(simulate(args.ticker, s, args.start, args.end))
