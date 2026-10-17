"""
backtest.py — M6E London Close Fade

Strategy:
  Session trend  = direction of 8:00am → 11:50am ET move
  Entry          = 12:00pm ET bar close (London fix)
  Direction      = FADE the morning trend
  Target         = 20 ticks from entry  ($12.50/contract)
  Stop           = 10 ticks from entry  ($6.25/contract)
  Time exit      = 1:30pm ET
  Filter         = skip if 8am→noon range < MIN_RANGE ticks (no clear trend)

M6E specs:
  Tick size  : 0.000050 USD/EUR  (half-pip)
  Tick value : $0.625/contract
  Margin     : ~$440/contract

Usage:
  python backtest.py
  python backtest.py --target 20 --stop 10 --contracts 1
  python backtest.py --min-range 15 --target 30
"""
import argparse
import json
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ       = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data"
OUT_DIR  = Path(__file__).parent / "results"
OUT_DIR.mkdir(exist_ok=True)

# M6E constants (overridden by CLI args)
TICK_SIZE   = 0.000050   # USD/EUR per tick
TICK_VALUE  = 0.625      # $ per tick per contract
COMMISSION  = 1.50       # RT estimate: $0.25 IBKR + $0.20 member + $0.02 NFA × 2 sides ≈ $0.94
                         # Using $1.50 as conservative (adds ~half-pip slippage)
CONTRACTS   = 1


def ticks(price_diff: float) -> float:
    return round(price_diff / TICK_SIZE, 1)


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    df = df.sort_index()
    bad = (df["close"] < 0.80) | (df["close"] > 2.00)
    if bad.sum():
        print(f"  Dropped {bad.sum()} bad bars")
        df = df[~bad]
    return df


def run_backtest(
    df: pd.DataFrame,
    target_ticks: int  = 20,
    stop_ticks: int    = 10,
    min_range: int     = 15,   # minimum 8am-noon range in ticks to take trade
    use_trend_filter: bool = True,
) -> tuple[list, list]:

    trades = []
    daily  = []
    trading_days = sorted(set(df.index.date))

    for day in trading_days:
        date_str = str(day)
        d = df[df.index.date == day]

        # Morning session: 8:00am–11:50am ET
        morning = d.between_time("08:00", "11:50")
        # London close bar: 12:00pm
        noon_bar = d.between_time("12:00", "12:00")
        # Post-close window for trade execution
        post = d.between_time("12:01", "13:30")

        if morning.empty or noon_bar.empty or post.empty:
            daily.append({"date": date_str, "fired": False, "skip_reason": "NO_DATA"})
            continue

        # Morning trend: first bar open vs last bar close
        morning_open  = morning.iloc[0].open
        morning_close = morning.iloc[-1].close
        session_range = ticks(abs(morning_close - morning_open))

        # Skip if no clear directional trend (choppy day)
        if session_range < min_range:
            daily.append({"date": date_str, "fired": False,
                          "skip_reason": f"RANGE_TOO_SMALL_{session_range:.0f}t"})
            continue

        # Trend direction: +1 = morning went UP → we go SHORT (fade)
        trend_dir = 1 if morning_close > morning_open else -1
        direction = -trend_dir   # fade = opposite

        # Entry at 12:00pm bar close
        entry_price = noon_bar.iloc[-1].close
        entry_ts    = noon_bar.index[-1]

        target_price = entry_price + direction * target_ticks * TICK_SIZE
        stop_price   = entry_price - direction * stop_ticks   * TICK_SIZE

        # Simulate bar-by-bar through post-London window
        exit_price  = None
        exit_reason = "TIME"
        exit_ts     = None

        for ts, bar in post.iterrows():
            if direction == 1:   # long (fading a down move)
                if bar.low  <= stop_price:
                    exit_price = stop_price; exit_reason = "STOP";   exit_ts = ts; break
                if bar.high >= target_price:
                    exit_price = target_price; exit_reason = "TARGET"; exit_ts = ts; break
            else:                # short (fading an up move)
                if bar.high >= stop_price:
                    exit_price = stop_price; exit_reason = "STOP";   exit_ts = ts; break
                if bar.low  <= target_price:
                    exit_price = target_price; exit_reason = "TARGET"; exit_ts = ts; break

        if exit_price is None:
            exit_price = post.iloc[-1].close
            exit_ts    = post.index[-1]

        raw_ticks  = ticks((exit_price - entry_price) * direction)
        pnl        = round(raw_ticks * TICK_VALUE * CONTRACTS - COMMISSION * CONTRACTS, 2)

        trade = {
            "date":          date_str,
            "direction":     "LONG" if direction == 1 else "SHORT",
            "morning_range": round(session_range, 1),
            "entry":         round(entry_price, 6),
            "target":        round(target_price, 6),
            "stop":          round(stop_price, 6),
            "exit":          round(exit_price, 6),
            "exit_reason":   exit_reason,
            "pnl_ticks":     raw_ticks,
            "pnl_dollars":   pnl,
        }
        trades.append(trade)
        daily.append({"date": date_str, "fired": True, "pnl": pnl,
                      "exit_reason": exit_reason})

    return trades, daily


def print_summary(trades: list, daily: list, params: dict) -> dict:
    if not trades:
        print("No trades generated.")
        return {}

    df_t = pd.DataFrame(trades)
    df_t["date"] = pd.to_datetime(df_t["date"])
    df_t["year"] = df_t["date"].dt.year

    total      = len(df_t)
    wins       = df_t[df_t.pnl_dollars > 0]
    losses     = df_t[df_t.pnl_dollars <= 0]
    win_rate   = len(wins) / total * 100
    total_pnl  = df_t.pnl_dollars.sum()
    years      = (df_t["date"].max() - df_t["date"].min()).days / 365.25
    ann_pnl    = total_pnl / years if years > 0 else 0
    day_pnl    = ann_pnl / 252

    # Drawdown
    cum = df_t.pnl_dollars.cumsum()
    dd  = (cum - cum.cummax()).min()

    exits = df_t.exit_reason.value_counts()

    print()
    print("─" * 55)
    print("  M6E LONDON CLOSE FADE — BACKTEST RESULTS")
    print("─" * 55)
    print(f"  Data range   : {df_t.date.min().date()} → {df_t.date.max().date()}")
    print(f"  Params       : target={params['target_ticks']}t  stop={params['stop_ticks']}t"
          f"  min_range={params['min_range']}t  contracts={params['contracts']}")
    print(f"  Commission   : ${params['commission']:.2f} RT/contract")
    print("─" * 55)
    print(f"  Total trades : {total}")
    print(f"  Win rate     : {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Exits        : {dict(exits)}")
    print(f"  Avg winner   : ${wins.pnl_dollars.mean():+.2f}")
    print(f"  Avg loser    : ${losses.pnl_dollars.mean():+.2f}")
    print(f"  Expectancy   : ${df_t.pnl_dollars.mean():+.2f}/trade")
    print(f"  Total P&L    : ${total_pnl:+,.2f}")
    print(f"  Ann. P&L     : ${ann_pnl:+,.2f}/yr")
    print(f"  Per day avg  : ${day_pnl:+.2f}/trading day")
    print(f"  Max drawdown : ${dd:,.2f}")
    print("─" * 55)
    print()
    print("  Year-by-year:")
    for yr, g in df_t.groupby("year"):
        wr  = (g.pnl_dollars > 0).mean() * 100
        tot = g.pnl_dollars.sum()
        bar = "█" * max(0, int(tot / 50))
        neg = "▓" * max(0, int(-tot / 50))
        print(f"  {yr}  {len(g):3d}tr  WR={wr:.0f}%  ${tot:>+7,.0f}  {bar}{neg}")

    return {
        "total_trades": total, "win_rate": win_rate, "total_pnl": total_pnl,
        "ann_pnl": ann_pnl, "day_pnl": day_pnl, "max_drawdown": dd,
    }


def main():
    global TICK_VALUE, COMMISSION, CONTRACTS

    parser = argparse.ArgumentParser(description="M6E London Close Fade backtest")
    parser.add_argument("--data",       type=Path, default=DATA_DIR / "m6e_1m.csv")
    parser.add_argument("--target",     type=int,  default=20,
                        help="Take-profit ticks (default 20 = $12.50/contract)")
    parser.add_argument("--stop",       type=int,  default=10,
                        help="Stop-loss ticks (default 10 = $6.25/contract)")
    parser.add_argument("--min-range",  type=int,  default=15,
                        help="Min morning range ticks to qualify (default 15)")
    parser.add_argument("--contracts",  type=int,  default=1)
    parser.add_argument("--commission", type=float, default=1.50,
                        help="RT commission per contract (default $1.50)")
    parser.add_argument("--tick-value", type=float, default=0.625)
    args = parser.parse_args()

    TICK_VALUE  = args.tick_value
    COMMISSION  = args.commission
    CONTRACTS   = args.contracts

    params = {
        "target_ticks": args.target,
        "stop_ticks":   args.stop,
        "min_range":    args.min_range,
        "contracts":    args.contracts,
        "commission":   args.commission,
    }

    print(f"Loading {args.data} ...")
    df = load_data(args.data)
    print(f"  {len(df):,} bars | {len(set(df.index.date))} days"
          f" | {df.index[0].date()} → {df.index[-1].date()}")

    trades, daily = run_backtest(
        df,
        target_ticks = args.target,
        stop_ticks   = args.stop,
        min_range    = args.min_range,
    )

    summary = print_summary(trades, daily, params)

    pd.DataFrame(trades).to_csv(OUT_DIR / "trade_results.csv", index=False)
    pd.DataFrame(daily).to_csv(OUT_DIR / "daily_results.csv",  index=False)
    with open(OUT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    fired = sum(1 for d in daily if d.get("fired"))
    print(f"\n  Fired {fired}/{len(daily)} days ({fired/len(daily)*100:.1f}%)")
    print(f"  Results saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
