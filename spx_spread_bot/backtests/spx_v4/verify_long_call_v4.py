"""Long ATM call — does it make money buying and selling intraday?

User's hypothesis: "buy naked calls in the morning, sell them, at most we
lose the call option, else we make a small profit and exit out."

Test on 525 days of clean SPX 0DTE OTM-call quote data (2021-2024).

Rules (same as v4 IC):
- Spot derived from put-call parity at decision time (no look-ahead)
- Buy at ASK (real fill price for a buyer)
- Sell at BID (real fill price for a seller)
- $0.05 slippage each way (very generous — real spread is wider)
- P&L per trade reported honestly

Variations tested:
- Entry times: 09:35, 10:00, 10:30, 11:00, 13:00, 14:00
- Hold durations: 15 min, 30 min, 1 hr, 2 hr, until 15:45
- Strike: ATM (closest to spot)
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from verify_spx_v4 import (
    load_universe, derive_spot_from_parity, find_strike,
    at_or_after, get_quote_near, log, SPACING,
)
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

ET = ZoneInfo("America/New_York")
from datetime import timezone
UTC = timezone.utc


def simulate_long_call(expiry_index, ps_by_day, cs_by_day, par_by_day, td,
                        entry_h, entry_m, exit_h, exit_m,
                        slip_each_way=0.05):
    if td not in ps_by_day or td not in ps_by_day[td]:
        return {"status": "no_0dte"}

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET)
                            .replace(hour=entry_h, minute=entry_m).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET)
                           .replace(hour=exit_h, minute=exit_m).astimezone(UTC))

    spot = derive_spot_from_parity(expiry_index, par_by_day, td, entry_ts)
    if spot is None: return {"status": "no_spot"}

    # Find ATM call strike
    call_avail = cs_by_day[td][td]
    atm_call = find_strike(call_avail, spot)
    if atm_call is None: return {"status": "no_atm"}

    c_df = expiry_index.get((td, td, atm_call, "C"))
    if c_df is None: return {"status": "no_call_data"}

    # Entry: pay ASK + slippage
    e = at_or_after(c_df, entry_ts)
    if e is None: return {"status": "no_entry_quote"}
    entry_ask = float(e["ask_px_00"]) if pd.notna(e["ask_px_00"]) else None
    if entry_ask is None or entry_ask <= 0:
        return {"status": "no_entry_ask"}
    entry_cost = entry_ask + slip_each_way

    # Exit: receive BID - slippage
    x_b, x_a = get_quote_near(c_df, exit_ts, window_minutes=10)
    if x_b is None or pd.isna(x_b):
        # If bid is missing, option is worth ~0 (deep OTM at expiry)
        x_b = 0.0
    exit_proceeds = max(0, float(x_b) - slip_each_way)

    pnl = (exit_proceeds - entry_cost) * 100

    return {
        "status": "ok",
        "trade_date": td, "year": td.year, "dow": td.strftime("%a"),
        "spot": spot, "strike": atm_call,
        "entry_cost": entry_cost, "exit_proceeds": exit_proceeds,
        "pnl": pnl,
    }


def run(expiry_index, ps, cs, par, entry_h, entry_m, exit_h, exit_m, slip):
    results = []
    statuses = defaultdict(int)
    for td in sorted(ps.keys()):
        r = simulate_long_call(expiry_index, ps, cs, par, td,
                                entry_h, entry_m, exit_h, exit_m, slip)
        statuses[r["status"]] += 1
        if r["status"] == "ok":
            results.append(r)
    return pd.DataFrame(results), dict(statuses)


def bootstrap_p_positive(df, n=1000, seed=42):
    if df.empty: return 0.0
    rng = np.random.default_rng(seed)
    pnls = df["pnl"].values
    pos = sum(1 for _ in range(n) if rng.choice(pnls, size=len(pnls), replace=True).mean() > 0)
    return pos / n


if __name__ == "__main__":
    log("=" * 110)
    log("LONG ATM CALL — user hypothesis test")
    log("'Buy naked calls in morning, sell them, at most lose the option, else small profit'")
    log("525 days SPX 0DTE data (2021-2024), $0.05 slip each way (generous)")
    log("=" * 110)

    expiry_index, ps, cs, par = load_universe(2021, 2024)
    log(f"\nUniverse: {len(ps)} trading days\n")

    log(f"{'Entry':<8}{'Exit':<8}{'Hold':<10}{'N':<5}{'Win%':<7}{'Total':<12}{'Avg':<9}{'Best':<9}{'Worst':<9}{'P(pos)':<7}")
    log("-" * 100)

    combos = [
        # (entry_h, entry_m, exit_h, exit_m, label)
        (9, 35,  9, 50,  "15min"),
        (9, 35,  10, 0,  "25min"),
        (9, 35,  10, 30, "55min"),
        (9, 35,  15, 45, "EOD"),
        (10, 0,  10, 30, "30min"),
        (10, 0,  11, 0,  "1hr"),
        (10, 0,  12, 0,  "2hr"),
        (10, 0,  15, 45, "EOD"),
        (10, 30, 11, 0,  "30min"),
        (10, 30, 11, 30, "1hr"),
        (10, 30, 13, 0,  "2.5hr"),
        (10, 30, 15, 45, "EOD"),
        (11, 0,  11, 30, "30min"),
        (11, 0,  15, 45, "EOD"),
        (13, 0,  13, 30, "30min"),
        (13, 0,  15, 45, "2.75hr"),
        (14, 0,  14, 30, "30min"),
        (14, 0,  15, 45, "1.75hr"),
        (15, 0,  15, 30, "30min"),
    ]

    for eh, em, xh, xm, label in combos:
        df, statuses = run(expiry_index, ps, cs, par, eh, em, xh, xm, 0.05)
        if df.empty:
            log(f"{eh:02d}:{em:02d}   {xh:02d}:{xm:02d}   {label:<10}EMPTY ({statuses})")
            continue
        n = len(df); wins = (df["pnl"] > 0).sum()
        total = df["pnl"].sum()
        p_pos = bootstrap_p_positive(df, n=500)
        log(f"{eh:02d}:{em:02d}   {xh:02d}:{xm:02d}   {label:<10}{n:<5}{wins/n*100:<6.0f}%${total:<+11,.0f}${df['pnl'].mean():<+8.0f}${df['pnl'].max():<+8.0f}${df['pnl'].min():<+8.0f}{p_pos:<7.2f}")

    log("\n--- Sample trades (10:30 entry → 11:00 exit, 30-min hold) ---")
    df, _ = run(expiry_index, ps, cs, par, 10, 30, 11, 0, 0.05)
    log(f"{'Date':<12}{'Spot':<8}{'Strike':<8}{'Entry$':<8}{'Exit$':<8}{'PnL':<8}")
    log("-" * 60)
    for r in df.head(8).to_dict("records"):
        log(f"{str(r['trade_date']):<12}{r['spot']:<7.0f} {r['strike']:<7.0f} ${r['entry_cost']:<6.2f} ${r['exit_proceeds']:<6.2f} ${r['pnl']:<+7.0f}")
    log("...")
    for r in df.tail(8).to_dict("records"):
        log(f"{str(r['trade_date']):<12}{r['spot']:<7.0f} {r['strike']:<7.0f} ${r['entry_cost']:<6.2f} ${r['exit_proceeds']:<6.2f} ${r['pnl']:<+7.0f}")

    log("\n--- Distribution: 10:30→11:00 (30-min hold) ---")
    pnls = df["pnl"].values
    log(f"  Trades:           {len(pnls)}")
    log(f"  Win rate:         {(pnls > 0).mean()*100:.0f}%")
    log(f"  Mean P&L:         ${pnls.mean():+.0f}")
    log(f"  Median P&L:       ${np.median(pnls):+.0f}")
    log(f"  Best trade:       ${pnls.max():+.0f}")
    log(f"  Worst trade:      ${pnls.min():+.0f}")
    log(f"  Std dev:          ${pnls.std():.0f}")
    log(f"  Annual (252 td):  ${pnls.mean() * 252:+,.0f}")
