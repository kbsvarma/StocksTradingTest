"""
VWAP Reclaim Backtest on MES 1-minute data (2019–present)
MES tracks SPX tick-for-tick — VWAP logic identical to SPY.
Results expressed in R (risk units) then translated to SPY dollar terms.
"""
import pandas as pd
import numpy as np
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
SIGNAL_START   = "10:00"   # ignore first 30min noise
SIGNAL_END     = "14:00"   # no new entries after 2pm
EXIT_TIME      = "15:45"   # hard close
VIX_MAX        = 25.0
RISK_PER_TRADE = 400       # $ risk per trade (stop distance × shares)
REWARD_RATIO   = 2.0       # target = 2× risk
VOL_CONFIRM    = True      # require volume > 20-bar avg on reclaim candle

MES_FILE  = Path("spx_spread_bot/backtests/ten_year/market_data") # no MES daily here
MES_1MIN  = Path("mes_orb_bot/data/mes_1min.csv")
VIX_FILE  = Path("spx_spread_bot/backtests/ten_year/market_data/vix.csv")

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading MES 1-min data...")
df = pd.read_csv(MES_1MIN, parse_dates=["ts_event"])
df = df.rename(columns={"ts_event": "ts"})
df = df.sort_values("ts").reset_index(drop=True)

# Normalize to ET (data already in ET offset)
df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
df["date"]  = df["ts"].dt.date
df["time"]  = df["ts"].dt.strftime("%H:%M")

# RTH only: 9:30–16:00
df = df[(df["time"] >= "09:30") & (df["time"] <= "16:00")].copy()

print(f"  Rows after RTH filter: {len(df):,}")
print(f"  Date range: {df['date'].min()} → {df['date'].max()}")

# ── Load VIX daily ───────────────────────────────────────────────────────────
vix = pd.read_csv(VIX_FILE, parse_dates=["date"])
vix["date"] = vix["date"].dt.date
vix_map = dict(zip(vix["date"], vix["close"]))

# ── Backtest ─────────────────────────────────────────────────────────────────
results = []
trading_days = sorted(df["date"].unique())

for day in trading_days:
    vix_level = vix_map.get(day, 999)
    if vix_level > VIX_MAX:
        continue

    day_df = df[df["date"] == day].copy().reset_index(drop=True)
    if len(day_df) < 30:
        continue

    # Calculate VWAP from 9:30 open
    day_df["typical"] = (day_df["high"] + day_df["low"] + day_df["close"]) / 3
    day_df["cum_tpv"] = (day_df["typical"] * day_df["volume"]).cumsum()
    day_df["cum_vol"] = day_df["volume"].cumsum()
    day_df["vwap"] = day_df["cum_tpv"] / day_df["cum_vol"].replace(0, np.nan)

    # 20-bar rolling avg volume for confirmation
    day_df["avg_vol"] = day_df["volume"].rolling(20, min_periods=1).mean()

    # Signal window
    signal_df = day_df[
        (day_df["time"] >= SIGNAL_START) & (day_df["time"] <= SIGNAL_END)
    ].copy()

    if signal_df.empty:
        continue

    # Find first VWAP reclaim:
    # Need at least one bar below VWAP, then a bar that closes above VWAP
    trade_taken = False
    below_seen  = False

    for i, row in signal_df.iterrows():
        if row["close"] < row["vwap"]:
            below_seen = True
            continue

        if below_seen and row["close"] > row["vwap"]:
            # Volume confirm
            if VOL_CONFIRM and row["volume"] < row["avg_vol"]:
                below_seen = False
                continue

            # Entry
            entry_price = row["close"]
            entry_time  = row["time"]

            # Stop = low of reclaim candle - 0.10
            stop_price  = row["low"] - 0.10
            stop_dist   = entry_price - stop_price
            if stop_dist <= 0:
                below_seen = False
                continue

            target_price = entry_price + (stop_dist * REWARD_RATIO)

            # Simulate forward until target, stop, or time exit
            future = day_df[day_df["time"] > entry_time]
            outcome = "time_exit"
            exit_price = None

            for _, frow in future.iterrows():
                if frow["time"] >= EXIT_TIME:
                    exit_price = frow["close"]
                    outcome = "time_exit"
                    break
                if frow["low"] <= stop_price:
                    exit_price = stop_price
                    outcome = "stop"
                    break
                if frow["high"] >= target_price:
                    exit_price = target_price
                    outcome = "target"
                    break

            if exit_price is None:
                exit_price = day_df.iloc[-1]["close"]
                outcome = "time_exit"

            pnl_pts  = exit_price - entry_price
            pnl_r    = pnl_pts / stop_dist          # in R units
            pnl_usd  = pnl_r * RISK_PER_TRADE

            results.append({
                "date":        str(day),
                "entry_time":  entry_time,
                "entry":       round(entry_price, 2),
                "stop":        round(stop_price, 2),
                "target":      round(target_price, 2),
                "exit":        round(exit_price, 2),
                "outcome":     outcome,
                "pnl_r":       round(pnl_r, 3),
                "pnl_usd":     round(pnl_usd, 2),
                "vix":         round(vix_level, 1),
            })
            trade_taken = True
            break

# ── Analysis ─────────────────────────────────────────────────────────────────
res = pd.DataFrame(results)
if res.empty:
    print("No trades found.")
    exit()

res["year"] = pd.to_datetime(res["date"]).dt.year
res["win"]  = res["pnl_usd"] > 0

print(f"\n{'='*55}")
print(f"  VWAP RECLAIM BACKTEST — MES 1-MIN  ({res['date'].min()} → {res['date'].max()})")
print(f"  Risk per trade: ${RISK_PER_TRADE}  |  Target: {REWARD_RATIO}R  |  VIX cap: {VIX_MAX}")
print(f"{'='*55}")

total_days  = len(trading_days)
trade_days  = len(res)
win_count   = res["win"].sum()
loss_count  = len(res) - win_count

print(f"\n  Total trading days reviewed : {total_days}")
print(f"  Days with signal            : {trade_days} ({trade_days/total_days*100:.0f}%)")
print(f"  Win / Loss                  : {win_count} / {loss_count}")
print(f"  Win rate                    : {win_count/trade_days*100:.1f}%")
print(f"  Avg winner                  : ${res[res['win']]['pnl_usd'].mean():.0f}")
print(f"  Avg loser                   : ${res[~res['win']]['pnl_usd'].mean():.0f}")
print(f"  Avg trade                   : ${res['pnl_usd'].mean():.0f}")
print(f"  Total P&L                   : ${res['pnl_usd'].sum():,.0f}")
print(f"  Max single loss             : ${res['pnl_usd'].min():,.0f}")
print(f"  Max single win              : ${res['pnl_usd'].max():,.0f}")

# Drawdown
res_sorted = res.sort_values("date")
res_sorted["cumulative"] = res_sorted["pnl_usd"].cumsum()
res_sorted["peak"]       = res_sorted["cumulative"].cummax()
res_sorted["drawdown"]   = res_sorted["cumulative"] - res_sorted["peak"]
max_dd = res_sorted["drawdown"].min()
print(f"  Max drawdown                : ${max_dd:,.0f}")

print(f"\n  {'Year':<8} {'Trades':>7} {'Win%':>6} {'P&L':>10} {'Per Trade Day':>14}")
print(f"  {'-'*50}")
for year, grp in res.groupby("year"):
    yr_wr  = grp["win"].mean() * 100
    yr_pnl = grp["pnl_usd"].sum()
    yr_avg = grp["pnl_usd"].mean()
    print(f"  {year:<8} {len(grp):>7} {yr_wr:>5.1f}% {yr_pnl:>10,.0f} {yr_avg:>14,.0f}")

print(f"\n  Outcome breakdown:")
for outcome, grp in res.groupby("outcome"):
    print(f"    {outcome:<12}: {len(grp)} trades, avg P&L ${grp['pnl_usd'].mean():.0f}")

res.to_csv("webull_vwap_backtest_results.csv", index=False)
print(f"\n  Full results saved to webull_vwap_backtest_results.csv")
