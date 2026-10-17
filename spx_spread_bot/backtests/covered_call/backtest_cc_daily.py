"""Covered-call weekly overlay backtest — honest version.

Data: daily OHLCV option bars (ohlcv-1d) for ATM-band weekly calls, 2024-2026.
Stock prices: yfinance daily (Monday open = entry, Friday close = settlement).

Model (standard CC overlay, hold 100 shares continuously):
  Each Mon: sell 1 weekly call at chosen strike (ATM / +1 / +2 OTM).
            Premium received = Monday option close - slippage.
  Each Fri: call expires.
     if Fri_close <= strike: expires worthless, keep premium, keep stock.
     if Fri_close >  strike: assigned, stock called away at strike (miss upside),
                             rebuy Monday to continue.

  Weekly P&L (per 100 sh):
     stock_leg   = (min(Fri_close, strike) - Mon_entry) * 100
     premium_leg = premium * 100
     total       = stock_leg + premium_leg

  Compare against buy & hold:  (Fri_close - Mon_entry) * 100

Reports: premium income (the "yield"), total CC return, buy&hold return,
         assignment rate, worst weeks, annualized.
"""
import re
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

DATA = Path(__file__).parent / "data"
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

SLIPPAGE = 0.03  # $ per share, subtracted from premium (half-spread haircut)


def parse_sym(s):
    m = SYM_RE.match(s.strip())
    if not m:
        return None
    root, yymmdd, right, strike_str = m.groups()
    return {
        "root": root,
        "expiry": pd.Timestamp(f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}").date(),
        "right": right,
        "strike": int(strike_str) / 1000,
    }


def load_option_daily(ticker):
    df = pd.read_parquet(DATA / f"{ticker.lower()}_cc_daily.parquet")
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["ts_event"]).dt.tz_localize(None).dt.date
    parsed = df["symbol"].apply(parse_sym)
    df["expiry"] = parsed.apply(lambda p: p["expiry"] if p else None)
    df["strike"] = parsed.apply(lambda p: p["strike"] if p else None)
    df["right"] = parsed.apply(lambda p: p["right"] if p else None)
    df = df.dropna(subset=["expiry", "strike", "right"])
    df = df[df["right"] == "C"]
    # Aggregate across publishers: volume-weighted close per (date, expiry, strike)
    def vw_close(g):
        v = g["volume"].sum()
        if v > 0:
            return (g["close"] * g["volume"]).sum() / v
        return g["close"].mean()
    agg = (df.groupby(["date", "expiry", "strike"])
             .apply(lambda g: pd.Series({
                 "close": vw_close(g),
                 "volume": g["volume"].sum(),
             }))
             .reset_index())
    return agg


def find_fridays(start, end):
    pairs = []
    d = start
    while d.weekday() != 0:
        d += pd.Timedelta(days=1)
    while d <= end:
        fri = d + pd.Timedelta(days=4)
        if fri <= end:
            pairs.append((d.date(), fri.date()))
        d += pd.Timedelta(days=7)
    return pairs


def backtest(ticker, otm_steps=0, grid=1.0, verbose=False,
             start_str="2024-01-01", end_str="2026-05-22"):
    opt = load_option_daily(ticker)
    # yfinance stock daily
    stk = yf.Ticker(ticker).history(start=start_str, end=end_str, auto_adjust=False)
    stk.index = pd.to_datetime(stk.index).tz_localize(None)
    stk_by_date = {d.date(): row for d, row in stk.iterrows()}

    start = pd.Timestamp(start_str); end = pd.Timestamp(end_str)
    weeks = find_fridays(start, end)

    # index option by (expiry, date) for quick strike lookup
    opt_idx = {}
    for _, r in opt.iterrows():
        opt_idx.setdefault((r["expiry"], r["date"]), []).append((r["strike"], r["close"], r["volume"]))

    results = []
    for mon, fri in weeks:
        # nearest available stock trading day on/after Monday for entry
        entry_day = None
        for off in range(0, 3):
            d = mon + pd.Timedelta(days=off)
            if d in stk_by_date:
                entry_day = d; break
        if entry_day is None:
            continue
        # Friday close (or nearest before)
        exit_day = None
        for off in range(0, 4):
            d = fri - pd.Timedelta(days=off)
            if d in stk_by_date:
                exit_day = d; break
        if exit_day is None:
            continue

        mon_spot = float(stk_by_date[entry_day]["Open"])
        fri_close = float(stk_by_date[exit_day]["Close"])

        atm = round(mon_spot / grid) * grid
        strike = atm + otm_steps * grid

        # Find this strike's Monday option price (premium)
        candidates = opt_idx.get((fri, entry_day), [])
        prem = None
        for k, c, v in candidates:
            if abs(k - strike) < 1e-6:
                prem = c; break
        if prem is None or prem <= 0 or np.isnan(prem):
            continue

        premium = max(0, prem - SLIPPAGE)
        assigned = fri_close > strike
        stock_leg = (min(fri_close, strike) - mon_spot) * 100
        premium_leg = premium * 100
        cc_total = stock_leg + premium_leg
        bh_total = (fri_close - mon_spot) * 100

        results.append({
            "mon": entry_day, "fri": exit_day,
            "mon_spot": mon_spot, "fri_close": fri_close,
            "strike": strike, "premium": premium,
            "assigned": assigned,
            "premium_inc": premium_leg,
            "cc_total": cc_total,
            "bh_total": bh_total,
            "year": entry_day.year,
        })

    return pd.DataFrame(results)


def summarize(df, ticker, label):
    if df.empty:
        print(f"{ticker} {label}: NO TRADES")
        return
    n = len(df)
    n_years = (df["fri"].max() - df["mon"].min()).days / 365.25
    prem_total = df["premium_inc"].sum()
    cc_total = df["cc_total"].sum()
    bh_total = df["bh_total"].sum()
    assign_rate = df["assigned"].mean() * 100
    avg_prem = df["premium_inc"].mean()
    worst_cc = df["cc_total"].min()
    print(f"{ticker:<6}{label:<10}N={n:<4}"
          f"PremInc/yr=${prem_total/n_years:<+8,.0f} "
          f"CC/yr=${cc_total/n_years:<+8,.0f} "
          f"B&H/yr=${bh_total/n_years:<+8,.0f} "
          f"Assign%={assign_rate:<4.0f} "
          f"AvgPrem=${avg_prem:<5.0f} "
          f"WorstWk=${worst_cc:<+7,.0f}")


def per_year_table(ticker, otm_steps, grid, start_str, end_str):
    df = backtest(ticker, otm_steps=otm_steps, grid=grid,
                  start_str=start_str, end_str=end_str)
    if df.empty:
        print(f"  NO TRADES")
        return
    print(f"  {'Year':<6}{'N':<5}{'PremInc':<11}{'CC total':<11}{'B&H':<11}"
          f"{'CCvsBH':<10}{'Assign%':<9}{'WorstWk':<9}")
    print("  " + "-" * 78)
    for yr in sorted(df["year"].unique()):
        s = df[df["year"] == yr]
        n = len(s)
        prem = s["premium_inc"].sum()
        cc = s["cc_total"].sum()
        bh = s["bh_total"].sum()
        edge = cc - bh
        asg = s["assigned"].mean() * 100
        worst = s["cc_total"].min()
        print(f"  {yr:<6}{n:<5}${prem:<+9,.0f} ${cc:<+9,.0f} ${bh:<+9,.0f} "
              f"${edge:<+8,.0f} {asg:<8.0f} ${worst:<+7,.0f}")
    # totals
    n = len(df); ny = (df["fri"].max() - df["mon"].min()).days / 365.25
    print("  " + "-" * 78)
    print(f"  {'ALL':<6}{n:<5}${df['premium_inc'].sum():<+9,.0f} "
          f"${df['cc_total'].sum():<+9,.0f} ${df['bh_total'].sum():<+9,.0f} "
          f"${df['cc_total'].sum()-df['bh_total'].sum():<+8,.0f} "
          f"{df['assigned'].mean()*100:<8.0f} ${df['cc_total'].min():<+7,.0f}")
    print(f"  Annualized: PremInc=${df['premium_inc'].sum()/ny:+,.0f}/yr  "
          f"CC=${df['cc_total'].sum()/ny:+,.0f}/yr  "
          f"B&H=${df['bh_total'].sum()/ny:+,.0f}/yr")


GRIDS = {"XLF": 1.0, "SOFI": 0.5, "WFC": 1.0, "OXY": 1.0, "T": 0.5,
         "HPQ": 0.5, "MDT": 1.0, "VALE": 0.5, "PFE": 0.5}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--peryear", action="store_true")
    args = ap.parse_args()

    t = args.ticker
    grid = GRIDS.get(t, 1.0)

    if args.peryear:
        print("=" * 95)
        print(f"COVERED CALL per-year breakdown — {t}  ({args.start} to {args.end})")
        print(f"Hold 100sh + sell weekly call. Slippage ${SLIPPAGE}/sh. "
              f"(NOTE: T spinoff Apr-2022 distorts B&H, not premium income)")
        print("=" * 95)
        for steps, lbl in [(0, "ATM"), (1, "+1 OTM"), (2, "+2 OTM")]:
            print(f"\n--- {lbl} ---")
            per_year_table(t, steps, grid, args.start, args.end)
    else:
        print("=" * 130)
        print(f"COVERED CALL weekly overlay — {t} {args.start} to {args.end}")
        print("=" * 130)
        for steps, lbl in [(0, "ATM"), (1, "+1 OTM"), (2, "+2 OTM")]:
            df = backtest(t, otm_steps=steps, grid=grid,
                          start_str=args.start, end_str=args.end)
            summarize(df, t, lbl)
