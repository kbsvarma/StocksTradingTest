"""Backtest: BAC + weekly ATM covered call vs buy-and-hold BAC.

Strategy:
  - Buy 100 BAC shares Jan 2024 (cost basis)
  - Every Monday 09:35 ET: sell 1 weekly ATM call (Fri expiry)
  - Friday close: settle option to intrinsic (cash-settled by Databento for backtest)
    - If BAC_close <= strike: keep premium, keep shares
    - If BAC_close > strike: keep premium, share called away → rebuy Monday at open
  - Track: cumulative premium, share P&L, dividend income, total return
  - Compare to buy-and-hold BAC over same period

Inputs:
  - data/bac_cc_weekly.parquet — option quotes (from pull script)
  - yfinance BAC daily history + dividends
"""
import re
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

DATA_FP = Path(__file__).parent / "data" / "bac_cc_weekly.parquet"

RX_STRIKE = re.compile(r"C(\d{8})$")
def parse_strike(s):
    m = RX_STRIKE.search(s.strip())
    return int(m.group(1))/1000 if m else None

def main():
    # Load option data
    print(f"[load] {DATA_FP}")
    df = pd.read_parquet(DATA_FP)
    print(f"  rows={len(df)} weeks={df['monday'].nunique()}")
    df["strike"] = df["symbol"].apply(parse_strike)
    df = df[df["bid_px_00"]>0].copy()
    df["mid"] = (df["bid_px_00"] + df["ask_px_00"]) / 2
    print(f"  valid quotes: {len(df)}")

    # For each Monday: select ATM strike (the one == atm_strike)
    atm = df[df["strike"] == df["atm_strike"]].copy()
    atm = atm.sort_values("monday").drop_duplicates("monday")
    print(f"  ATM trades: {len(atm)} weeks")

    # Load BAC daily + dividends
    print(f"[yf] BAC daily + dividends")
    t = yf.Ticker("BAC")
    hist = t.history(start="2024-01-01", end="2026-05-23", auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    divs = t.dividends
    divs.index = pd.to_datetime(divs.index).tz_localize(None)
    div_in_range = divs[(divs.index >= "2024-01-01") & (divs.index <= "2026-05-22")]
    print(f"  BAC days: {len(hist)}, dividends: {len(div_in_range)} payments totaling ${div_in_range.sum():.2f}/share")

    # ===== STRATEGY 1: Covered Call =====
    initial_spot = float(hist.iloc[0]["Open"])
    shares = 100
    cost_basis = initial_spot * shares  # initial investment
    print(f"\n[CC strategy] initial buy {shares} BAC @ ${initial_spot:.2f} = ${cost_basis:,.0f}")

    cc_log = []
    held_shares = shares
    cum_premium = 0.0  # bid-side (realistic sell credit)
    cum_premium_mid = 0.0
    cum_div = 0.0
    rebuy_cost = 0.0
    assignments = 0

    for _, row in atm.iterrows():
        mon = pd.Timestamp(row["monday"])
        fri = pd.Timestamp(row["friday"])
        strike = float(row["strike"])
        # Sell call at BID (realistic) and MID (theoretical)
        prem_bid = float(row["bid_px_00"])
        prem_mid = float(row["mid"])
        # Need Friday close from hist
        # find nearest trading day <= friday
        try:
            fri_idx = hist.index.get_indexer([fri], method="ffill")[0]
            if fri_idx < 0: continue
            fri_close = float(hist.iloc[fri_idx]["Close"])
        except Exception:
            continue

        cum_premium     += prem_bid * 100  # contract * 100 shares
        cum_premium_mid += prem_mid * 100

        if fri_close > strike:
            # Called away — shares sold at strike
            # If we had 100 shares: receive 100 * strike, lose shares
            # Next Monday: rebuy at open (or live with no shares — for simplicity assume immediate rebuy)
            assignments += 1
            # The premium is already collected. The loss vs holding = (fri_close - strike)*100 missed gain
            # But we sold at strike, so gain = (strike - cost_at_start)*100
            # Simplification: track as rolling — close call by paying intrinsic
            # Net P&L for the trade: premium - max(0, fri_close - strike)*100
            # (this models rolling rather than actual assignment + rebuy)
            intrinsic = (fri_close - strike) * 100
            cum_premium     -= intrinsic   # net of buy-back at intrinsic
            cum_premium_mid -= intrinsic
        cc_log.append({"monday": mon, "strike": strike, "prem_bid": prem_bid,
                       "fri_close": fri_close, "assigned": fri_close > strike,
                       "cum_premium_real": cum_premium, "cum_premium_mid": cum_premium_mid})

    # Dividend income (assume held throughout — for simplicity, ignore divs on assigned-away weeks)
    cum_div = div_in_range.sum() * shares

    # Final shares value
    final_spot = float(hist.iloc[-1]["Close"])
    final_shares_value = held_shares * final_spot

    cc_total_real = (final_shares_value - cost_basis) + cum_premium + cum_div
    cc_total_mid  = (final_shares_value - cost_basis) + cum_premium_mid + cum_div

    print(f"\n[CC result] (sold at BID for realistic, no slippage)")
    print(f"  Weeks traded: {len(atm)}")
    print(f"  Assigned/ITM weeks: {assignments} ({assignments/len(atm)*100:.0f}%)")
    print(f"  Initial cost basis: ${cost_basis:,.0f}")
    print(f"  Final shares value: ${final_shares_value:,.0f}  (BAC ${initial_spot:.2f}→${final_spot:.2f})")
    print(f"  Cumulative premium [REAL/bid]: ${cum_premium:,.0f}")
    print(f"  Cumulative premium [MID]:      ${cum_premium_mid:,.0f}")
    print(f"  Cumulative dividends:           ${cum_div:,.0f}")
    print(f"  TOTAL P&L [real]: ${cc_total_real:,.0f}")
    print(f"  TOTAL P&L [mid]:  ${cc_total_mid:,.0f}")

    # ===== STRATEGY 2: Buy and Hold =====
    bh_total = (final_spot - initial_spot) * shares + cum_div
    print(f"\n[B&H result]")
    print(f"  Initial: ${cost_basis:,.0f}  Final shares: ${final_shares_value:,.0f}")
    print(f"  Dividends: ${cum_div:,.0f}")
    print(f"  TOTAL P&L: ${bh_total:,.0f}")

    # ===== COMPARISON =====
    yrs = (hist.index[-1] - hist.index[0]).days / 365.25
    print(f"\n=== COMPARISON (over {yrs:.2f} years) ===")
    print(f"  Buy & Hold:       ${bh_total:,.0f}  ({bh_total/cost_basis*100/yrs:+.1f}%/yr)")
    print(f"  CC (real fills):  ${cc_total_real:,.0f}  ({cc_total_real/cost_basis*100/yrs:+.1f}%/yr)")
    print(f"  CC (mid fills):   ${cc_total_mid:,.0f}  ({cc_total_mid/cost_basis*100/yrs:+.1f}%/yr)")
    print(f"  CC advantage [real]: ${cc_total_real - bh_total:+,.0f}")

    # Per-year breakdown
    print(f"\n=== PER-YEAR BREAKDOWN (CC strategy) ===")
    cc_df = pd.DataFrame(cc_log)
    cc_df["year"] = pd.to_datetime(cc_df["monday"]).dt.year
    # Premium collected per year:
    for y, grp in cc_df.groupby("year"):
        # compute change in cum_premium_real over the year
        n = len(grp)
        prem_year_real = grp["cum_premium_real"].iloc[-1] - (grp["cum_premium_real"].iloc[0] - grp["prem_bid"].iloc[0]*100)
        # Use first row's premium added back as initial guess
        # Simpler: sum the per-week premium credits (manually recompute)
        weekly_prem = []
        for i, r in grp.iterrows():
            wk_prem = r["prem_bid"]*100
            if r["assigned"]:
                wk_prem -= (r["fri_close"] - r["strike"])*100
            weekly_prem.append(wk_prem)
        total_year_prem = sum(weekly_prem)
        n_assigned = grp["assigned"].sum()
        print(f"  {y}: {n} weeks, {n_assigned} assigned ({n_assigned/n*100:.0f}%), premium net=${total_year_prem:+,.0f}")

if __name__ == "__main__":
    main()
