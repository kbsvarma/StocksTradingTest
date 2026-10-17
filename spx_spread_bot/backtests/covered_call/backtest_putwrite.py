"""SPY monthly PUT-WRITE backtest — defensive income on $25k.

Models the CBOE PutWrite-style strategy:
  - Account = $25,000 cash.
  - Each monthly roll (3rd Friday): sell N cash-secured OTM puts expiring next
    3rd Friday, where N = floor(cash_collateral / (strike*100)).
  - Hold to expiry. Payoff = premium - max(0, strike - SPY_expiry).
  - Uncommitted cash + collateral earns a T-bill proxy (annualized, applied monthly).
  - If assigned (SPY < strike at expiry), the loss is realized in cash (we model
    cash-settled equivalent: we don't take stock, we just realize the put loss;
    economically identical to being assigned then immediately selling).

Reports per-year incl 2018/2020/2022, max drawdown, vs SPY buy&hold, vs T-bills.

Honest expectation stated before running: put-write captures the index vol
risk premium — historically ~equity-like return with LOWER volatility, but
real tail losses in crashes. Not a jackpot.
"""
import re, sys, argparse
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

DATA = Path(__file__).parent / "data"
SYM_RE = re.compile(r"^(SPY)\s+(\d{6})([PC])(\d{8})$")
SLIPPAGE = 0.05  # $/share haircut on premium received

# Rough T-bill annual yield by year (3M T-bill avg) — conservative proxy
TBILL = {2014:0.0003,2015:0.0005,2016:0.003,2017:0.009,2018:0.019,2019:0.021,
         2020:0.004,2021:0.0005,2022:0.020,2023:0.050,2024:0.050,2025:0.045}


def third_friday(year, month):
    d = date(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d + timedelta(days=14)


def load():
    df = pd.read_parquet(DATA / "spy_putwrite_monthly.parquet").reset_index()
    df["date"] = pd.to_datetime(df["ts_event"]).dt.tz_localize(None).dt.date
    parsed = df["symbol"].apply(lambda s: SYM_RE.match(s.strip()))
    df = df[parsed.notna()].copy()
    df["expiry"] = [pd.Timestamp(f"20{m.group(2)[:2]}-{m.group(2)[2:4]}-{m.group(2)[4:6]}").date()
                    for m in parsed if m]
    df["strike"] = [int(m.group(4))/1000 for m in parsed if m]
    # volume-weighted close per (date, expiry, strike)
    g = df.groupby(["date","expiry","strike"])
    out = g.apply(lambda x: (x["close"]*x["volume"]).sum()/x["volume"].sum()
                  if x["volume"].sum()>0 else x["close"].mean(),
                  include_groups=False).reset_index(name="close")
    idx = {}
    for _, r in out.iterrows():
        idx.setdefault((r["expiry"], r["date"]), {})[r["strike"]] = r["close"]
    return idx


def run(otm_pct, start_year=2014, end_year=2025, capital=25000):
    opt = load()
    spy = yf.Ticker("SPY")
    h = spy.history(start=f"{start_year}-01-01", end=f"{end_year}-12-31", auto_adjust=False)
    h.index = pd.to_datetime(h.index).tz_localize(None)
    spot_by_date = {d.date(): r for d, r in h.iterrows()}
    divs = spy.dividends; divs.index = pd.to_datetime(divs.index).tz_localize(None)
    div_by_date = {d.date(): float(v) for d, v in divs.items()}

    def spot_on_after(day, fld="Open", maxoff=5):
        for off in range(maxoff+1):
            d = day + timedelta(days=off)
            if d in spot_by_date: return float(spot_by_date[d][fld]), d
        return None, None

    rolls = []
    for y in range(start_year, end_year+1):
        for m in range(1, 13):
            entry = third_friday(y, m)
            ny, nm = (y, m+1) if m < 12 else (y+1, 1)
            rolls.append((entry, third_friday(ny, nm)))

    cash = capital
    pw_log = []
    # buy & hold benchmark
    first_spot, _ = spot_on_after(rolls[0][0])
    bh_shares = capital / first_spot
    bh_div = 0.0

    for entry, expiry in rolls:
        spot, eday = spot_on_after(entry, "Open")
        exp_spot, xday = spot_on_after(expiry, "Close")
        if spot is None or exp_spot is None:
            continue
        yr = eday.year
        rf_month = TBILL.get(yr, 0.03) / 12

        # choose strike nearest to target OTM
        target = spot * (1 - otm_pct)
        quotes = opt.get((expiry, eday), {})
        if not quotes:
            # try a couple days around entry
            for off in range(1, 4):
                quotes = opt.get((expiry, eday + timedelta(days=off)), {})
                if quotes: break
        if not quotes:
            cash *= (1 + rf_month)  # idle month, earn rf
            continue
        strike = min(quotes.keys(), key=lambda k: abs(k - target))
        prem = quotes[strike]
        if prem is None or prem <= 0 or np.isnan(prem):
            cash *= (1 + rf_month); continue
        premium = max(0.0, prem - SLIPPAGE)

        # number of contracts: cash-secured by strike*100
        collateral_per = strike * 100
        n = int(cash // collateral_per)
        if n < 1:
            cash *= (1 + rf_month); continue

        # premium income
        prem_income = premium * 100 * n
        # settlement loss if assigned
        assigned = exp_spot < strike
        settle_loss = max(0.0, strike - exp_spot) * 100 * n
        # cash earns rf for the month (on full balance)
        interest = cash * rf_month

        cash = cash + prem_income - settle_loss + interest

        # buy&hold dividends over the month
        for d, amt in div_by_date.items():
            if eday <= d <= xday:
                bh_div += amt * bh_shares

        pw_log.append({
            "entry": eday, "expiry": xday, "year": yr,
            "spot": spot, "strike": strike, "exp_spot": exp_spot,
            "otm_actual": (spot-strike)/spot*100,
            "contracts": n, "premium": premium,
            "prem_income": prem_income, "settle_loss": settle_loss,
            "interest": interest, "assigned": assigned,
            "cash": cash,
            "bh_value": bh_shares * exp_spot + bh_div,
        })

    log = pd.DataFrame(pw_log)
    return log, capital


def report(otm_pct):
    log, cap = run(otm_pct)
    if log.empty:
        print(f"OTM {otm_pct*100:.0f}%: NO DATA"); return
    final = log["cash"].iloc[-1]
    bh_final = log["bh_value"].iloc[-1]
    yrs = (log["expiry"].iloc[-1] - log["entry"].iloc[0]).days / 365.25
    pw_cagr = (final/cap)**(1/yrs) - 1
    bh_cagr = (bh_final/cap)**(1/yrs) - 1

    # max drawdown of put-write equity curve
    eq = log["cash"].values
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min() * 100
    # buy&hold drawdown
    bh = log["bh_value"].values
    bhpeak = np.maximum.accumulate(bh)
    bh_dd = ((bh - bhpeak)/bhpeak).min() * 100

    print(f"\n{'='*84}")
    print(f"SPY PUT-WRITE  ~{otm_pct*100:.0f}% OTM monthly  —  ${cap:,.0f} start, {yrs:.1f}yr")
    print(f"{'='*84}")
    print(f"  PUT-WRITE final:  ${final:>11,.0f}   CAGR {pw_cagr*100:+.1f}%/yr   maxDD {max_dd:.0f}%")
    print(f"  SPY buy&hold:     ${bh_final:>11,.0f}   CAGR {bh_cagr*100:+.1f}%/yr   maxDD {bh_dd:.0f}%")
    print(f"  Assignment rate:  {log['assigned'].mean()*100:.0f}%   "
          f"avg contracts/mo: {log['contracts'].mean():.1f}")
    tot_prem = log["prem_income"].sum(); tot_loss = log["settle_loss"].sum()
    tot_int = log["interest"].sum()
    print(f"  Gross premium collected: ${tot_prem:,.0f}   "
          f"Assignment losses: ${tot_loss:,.0f}   Interest: ${tot_int:,.0f}")
    print(f"  Net from options: ${tot_prem-tot_loss:,.0f}  over {yrs:.1f}yr "
          f"= ${(tot_prem-tot_loss)/yrs:,.0f}/yr")
    print(f"\n  {'Year':<6}{'PW end$':<12}{'PW yr%':<9}{'BH end$':<12}{'BH yr%':<9}{'Assign%':<9}{'WorstMo$':<10}")
    print("  " + "-"*70)
    prev_pw = cap; prev_bh = cap
    for yr in sorted(log["year"].unique()):
        s = log[log["year"] == yr]
        pw_end = s["cash"].iloc[-1]; bh_end = s["bh_value"].iloc[-1]
        pw_yr = (pw_end/prev_pw - 1)*100; bh_yr = (bh_end/prev_bh - 1)*100
        worst = (s["prem_income"] - s["settle_loss"]).min()
        print(f"  {yr:<6}${pw_end:<10,.0f} {pw_yr:<+8.1f}%${bh_end:<10,.0f} {bh_yr:<+8.1f}%"
              f"{s['assigned'].mean()*100:<8.0f} ${worst:<+9,.0f}")
        prev_pw = pw_end; prev_bh = bh_end


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--otm", type=float, default=None)
    args = ap.parse_args()
    levels = [args.otm] if args.otm is not None else [0.02, 0.05, 0.08]
    print("="*84)
    print("DEFENSIVE INCOME TEST: SPY monthly cash-secured put-write, $25k, 2014-2025")
    print("Honest prior: captures index vol-risk-premium; ~equity-like return, lower vol,")
    print("              but real tail losses in crashes. Slippage $0.05/sh.")
    print("="*84)
    for o in levels:
        report(o)
