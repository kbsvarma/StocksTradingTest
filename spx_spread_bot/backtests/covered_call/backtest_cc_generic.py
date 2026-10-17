"""Generic CC backtest: ticker + weekly ATM call vs buy-and-hold.
Usage: python backtest_cc_generic.py --ticker MO
"""
import argparse, re
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
RX_STRIKE = re.compile(r"C(\d{8})$")
def parse_strike(s):
    m = RX_STRIKE.search(s.strip()); return int(m.group(1))/1000 if m else None

def run(ticker, start_date="2024-01-01", end_date="2026-05-23"):
    ticker = ticker.upper()
    fp = DATA_DIR / f"{ticker.lower()}_cc_weekly.parquet"
    if not fp.exists():
        print(f"No data file at {fp}"); return None
    df = pd.read_parquet(fp)
    df["strike"] = df["symbol"].apply(parse_strike)
    df = df[df["bid_px_00"]>0].copy()
    df["mid"] = (df["bid_px_00"] + df["ask_px_00"]) / 2
    # Split entry vs exit windows
    if "window" not in df.columns:
        print(f"  WARNING: no window column in {fp.name} — old data, exit modeled as intrinsic")
        df["window"] = "entry"
    entry_df = df[df["window"]=="entry"].copy()
    exit_df  = df[df["window"]=="exit"].copy()
    atm = entry_df[entry_df["strike"] == entry_df["atm_strike"]].copy().sort_values("monday").drop_duplicates("monday")

    t = yf.Ticker(ticker)
    hist = t.history(start=start_date, end=end_date, auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    divs = t.dividends
    divs.index = pd.to_datetime(divs.index).tz_localize(None)
    divs = divs[(divs.index >= start_date) & (divs.index <= end_date)]
    initial_spot = float(hist.iloc[0]["Open"])
    final_spot = float(hist.iloc[-1]["Close"])
    shares = 100
    cost_basis = initial_spot * shares
    cum_div = divs.sum() * shares

    cc_real = 0.0; cc_mid = 0.0; assigned = 0
    weekly = []
    for _, row in atm.iterrows():
        mon = pd.Timestamp(row["monday"]); fri = pd.Timestamp(row["friday"])
        strike = float(row["strike"])
        pr_bid = float(row["bid_px_00"]); pr_mid = float(row["mid"])
        fri_idx = hist.index.get_indexer([fri], method="ffill")[0]
        if fri_idx < 0: continue
        fri_close = float(hist.iloc[fri_idx]["Close"])
        cc_real += pr_bid * 100; cc_mid += pr_mid * 100
        is_assigned = fri_close > strike
        if is_assigned:
            intrinsic = (fri_close - strike) * 100
            cc_real -= intrinsic; cc_mid -= intrinsic
            assigned += 1
        weekly.append({"monday": mon, "strike": strike, "prem_bid": pr_bid,
                       "fri_close": fri_close, "assigned": is_assigned,
                       "weekly_net": (pr_bid * 100) - ((fri_close - strike) * 100 if is_assigned else 0)})

    final_shares_value = shares * final_spot
    cc_total_real = (final_shares_value - cost_basis) + cc_real + cum_div
    cc_total_mid  = (final_shares_value - cost_basis) + cc_mid + cum_div
    bh_total = (final_spot - initial_spot) * shares + cum_div
    yrs = (hist.index[-1] - hist.index[0]).days / 365.25

    print(f"\n{'='*80}")
    print(f"{ticker}: {initial_spot:.2f} → {final_spot:.2f} ({(final_spot/initial_spot-1)*100:+.1f}% over {yrs:.2f}yr)")
    print(f"{'='*80}")
    print(f"  Weeks traded: {len(atm)}, assigned: {assigned} ({assigned/max(1,len(atm))*100:.0f}%)")
    print(f"  Capital (cost basis 100 sh): ${cost_basis:,.0f}")
    print(f"  Final shares value:         ${final_shares_value:,.0f}")
    print(f"  Cumulative premium [REAL/bid]: ${cc_real:+,.0f}")
    print(f"  Cumulative dividends:          ${cum_div:+,.0f}")
    print(f"  TOTAL CC P&L [real]: ${cc_total_real:+,.0f}  ({cc_total_real/cost_basis*100/yrs:+.1f}%/yr)")
    print(f"  TOTAL B&H P&L:       ${bh_total:+,.0f}  ({bh_total/cost_basis*100/yrs:+.1f}%/yr)")
    diff = cc_total_real - bh_total
    print(f"  CC vs B&H:           ${diff:+,.0f}  ({'CC WINS' if diff > 0 else 'B&H WINS'})")
    # Yearly
    wk_df = pd.DataFrame(weekly)
    wk_df["year"] = pd.to_datetime(wk_df["monday"]).dt.year
    print(f"  Per-year premium net (bid - intrinsic):")
    for y, grp in wk_df.groupby("year"):
        net = grp["weekly_net"].sum(); n = len(grp); na = grp["assigned"].sum()
        print(f"    {y}: n={n} assigned={na} ({na/n*100:.0f}%) net=${net:+,.0f}")
    return {"ticker":ticker, "weeks":len(atm), "assigned":assigned,
            "cost_basis":cost_basis, "cc_total":cc_total_real, "bh_total":bh_total,
            "diff":diff, "yrs":yrs}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="MO,VZ,GILD")
    args = ap.parse_args()
    summary = []
    for t in args.tickers.split(","):
        r = run(t.strip())
        if r: summary.append(r)
    if summary:
        print(f"\n\n{'='*80}\nSUMMARY")
        print(f"{'='*80}")
        print(f"{'TICKER':<8} {'CAP':>10} {'CC':>12} {'B&H':>12} {'DIFF':>10} {'CC ROI/yr':>12} {'B&H ROI/yr':>12}")
        for r in summary:
            print(f"{r['ticker']:<8} ${r['cost_basis']:>9,.0f} ${r['cc_total']:>+11,.0f} ${r['bh_total']:>+11,.0f} ${r['diff']:>+9,.0f} {r['cc_total']/r['cost_basis']*100/r['yrs']:>11.1f}% {r['bh_total']/r['cost_basis']*100/r['yrs']:>11.1f}%")
