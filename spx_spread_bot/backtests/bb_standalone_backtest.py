"""
Bollinger Band Standalone Strategy Backtest
===========================================
Tests three pure BB strategies on SPX daily data (2013–2026).
No options pricing. Each strategy trades SPX directly (1 unit = $1/point).

Strategies
----------
1. Mean Reversion  — long when close < lower band, exit at mid or after N days
2. Breakout        — long when close > upper band (momentum), exit when close < mid
                     short when close < lower band, exit when close > mid
3. Squeeze+Break   — wait for BB squeeze (width < threshold for K days),
                     then trade the first directional close outside the band

Comparison baseline: BPS 13-year results
  453 trades | $183,312 total | 75.5% win rate | Sharpe 4.59 | MaxDD -$9,756
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BB_CSV = Path(__file__).parent / "ten_year" / "spx_daily_ohlc_bb.csv"

# position sizing: $50 per SPX point (approx 1 ES/MES mini contract scaling)
DOLLARS_PER_POINT = 50


# ── data ──────────────────────────────────────────────────────────────────────

def load_bb() -> pd.DataFrame:
    df = pd.read_csv(BB_CSV, index_col=0, parse_dates=True)
    df.index.name = "date"
    df = df[df["bb_upper"].notna()].copy()
    return df


# ── metrics ───────────────────────────────────────────────────────────────────

def metrics(trades: list[dict], label: str) -> dict:
    if not trades:
        return dict(label=label, trades=0, total_pnl=0, win_rate=0,
                    avg_pnl=0, max_dd=0, sharpe=0)
    df = pd.DataFrame(trades)
    pnls = df["pnl"].values
    wins = (pnls > 0).sum()
    cum = np.cumsum(pnls)
    dd = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(pnls.mean())
    std = float(pnls.std(ddof=1)) if len(pnls) > 1 else 1.0
    sharpe = (avg / std) * np.sqrt(252) if std > 0 else 0.0
    return dict(
        label=label,
        trades=len(df),
        total_pnl=round(float(pnls.sum()), 2),
        win_rate=round(wins / len(df), 3),
        avg_pnl=round(avg, 2),
        max_dd=round(dd, 2),
        sharpe=round(sharpe, 3),
    )


# ── strategy 1: mean reversion ────────────────────────────────────────────────

def strategy_mean_reversion(df: pd.DataFrame, max_hold: int = 10) -> list[dict]:
    """
    Entry : close < bb_lower  (price pierces lower band → oversold)
    Exit  : close >= bb_mid   OR held max_hold days (whichever first)
    Direction: long only (fading downside exhaustion)
    """
    trades = []
    in_trade = False
    entry_price = entry_date = hold_days = None

    for i, (dt, row) in enumerate(df.iterrows()):
        if in_trade:
            hold_days += 1
            exit_now = row["close"] >= row["bb_mid"] or hold_days >= max_hold
            if exit_now:
                pnl = (row["close"] - entry_price) * DOLLARS_PER_POINT
                trades.append(dict(entry=entry_date, exit=dt,
                                   entry_px=entry_price, exit_px=row["close"],
                                   pnl=pnl, hold=hold_days))
                in_trade = False
        if not in_trade and row["close"] < row["bb_lower"]:
            in_trade = True
            entry_price = row["close"]
            entry_date = dt
            hold_days = 0

    return trades


# ── strategy 2: breakout / momentum ──────────────────────────────────────────

def strategy_breakout(df: pd.DataFrame) -> list[dict]:
    """
    Long  entry : close > bb_upper  (breakout to upside)
    Long  exit  : close < bb_mid

    Short entry : close < bb_lower  (breakout to downside)
    Short exit  : close > bb_mid

    One trade at a time. No pyramiding.
    """
    trades = []
    position = None  # 'long' | 'short' | None
    entry_price = entry_date = None

    for dt, row in df.iterrows():
        if position == "long":
            if row["close"] < row["bb_mid"]:
                pnl = (row["close"] - entry_price) * DOLLARS_PER_POINT
                trades.append(dict(entry=entry_date, exit=dt, direction="long",
                                   entry_px=entry_price, exit_px=row["close"], pnl=pnl))
                position = None

        elif position == "short":
            if row["close"] > row["bb_mid"]:
                pnl = (entry_price - row["close"]) * DOLLARS_PER_POINT
                trades.append(dict(entry=entry_date, exit=dt, direction="short",
                                   entry_px=entry_price, exit_px=row["close"], pnl=pnl))
                position = None

        if position is None:
            if row["close"] > row["bb_upper"]:
                position = "long"
                entry_price = row["close"]
                entry_date = dt
            elif row["close"] < row["bb_lower"]:
                position = "short"
                entry_price = row["close"]
                entry_date = dt

    return trades


# ── strategy 3: squeeze + breakout ───────────────────────────────────────────

def strategy_squeeze_breakout(df: pd.DataFrame,
                               squeeze_width: float = 0.04,
                               min_squeeze_days: int = 5,
                               max_hold: int = 20) -> list[dict]:
    """
    Squeeze : bb_width < squeeze_width for >= min_squeeze_days consecutive days
    Entry   : first close outside bb_upper (long) or bb_lower (short) after squeeze ends
    Exit    : close crosses back through bb_mid  OR  held max_hold days
    """
    trades = []
    squeeze_count = 0
    in_squeeze = False
    squeeze_armed = False
    position = None
    entry_price = entry_date = hold_days = direction = None

    for dt, row in df.iterrows():
        # track squeeze state
        if row["bb_width"] < squeeze_width:
            squeeze_count += 1
        else:
            if squeeze_count >= min_squeeze_days:
                squeeze_armed = True  # squeeze just ended — arm the signal
            squeeze_count = 0
        in_squeeze = row["bb_width"] < squeeze_width

        # manage open position
        if position is not None:
            hold_days += 1
            exit_now = hold_days >= max_hold
            if direction == "long":
                exit_now = exit_now or row["close"] < row["bb_mid"]
                if exit_now:
                    pnl = (row["close"] - entry_price) * DOLLARS_PER_POINT
                    trades.append(dict(entry=entry_date, exit=dt, direction="long",
                                       entry_px=entry_price, exit_px=row["close"], pnl=pnl))
                    position = None
            else:
                exit_now = exit_now or row["close"] > row["bb_mid"]
                if exit_now:
                    pnl = (entry_price - row["close"]) * DOLLARS_PER_POINT
                    trades.append(dict(entry=entry_date, exit=dt, direction="short",
                                       entry_px=entry_price, exit_px=row["close"], pnl=pnl))
                    position = None

        # look for entry after squeeze
        if squeeze_armed and position is None and not in_squeeze:
            if row["close"] > row["bb_upper"]:
                position = "long"
                direction = "long"
                entry_price = row["close"]
                entry_date = dt
                hold_days = 0
                squeeze_armed = False
            elif row["close"] < row["bb_lower"]:
                position = "short"
                direction = "short"
                entry_price = row["close"]
                entry_date = dt
                hold_days = 0
                squeeze_armed = False

    return trades


# ── output ────────────────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
    hdr = f"{'Strategy':<40} {'Trades':>7} {'Total PnL':>12} {'Win%':>6} {'Avg PnL':>9} {'Max DD':>11} {'Sharpe':>7}"
    sep = "-" * len(hdr)
    print(sep)
    print(hdr)
    print(sep)
    for r in rows:
        print(
            f"{r['label']:<40} {r['trades']:>7} "
            f"${r['total_pnl']:>11,.0f} {r['win_rate']*100:>5.1f}% "
            f"${r['avg_pnl']:>8,.0f} ${r['max_dd']:>10,.0f} {r['sharpe']:>7.3f}"
        )
    print(sep)


def trade_detail(trades: list[dict], label: str, n: int = 5) -> None:
    if not trades:
        return
    df = pd.DataFrame(trades)
    df["pnl"] = df["pnl"].round(0)
    print(f"\n{label} — worst {n} trades:")
    print(df.nsmallest(n, "pnl")[["entry", "exit", "pnl"]].to_string(index=False))
    print(f"{label} — best {n} trades:")
    print(df.nlargest(n, "pnl")[["entry", "exit", "pnl"]].to_string(index=False))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load_bb()
    print(f"SPX data: {df.index[0].date()} → {df.index[-1].date()}  ({len(df)} bars)\n")

    mr_trades  = strategy_mean_reversion(df)
    bo_trades  = strategy_breakout(df)
    sq_trades  = strategy_squeeze_breakout(df)

    bps_baseline = dict(
        label="── BPS baseline (options, for reference) ──",
        trades=453, total_pnl=183312, win_rate=0.755,
        avg_pnl=405, max_dd=-9756, sharpe=4.594,
    )

    rows = [
        bps_baseline,
        metrics(mr_trades,  "BB Mean Reversion (long fade)"),
        metrics(bo_trades,  "BB Breakout (long + short)"),
        metrics(sq_trades,  "BB Squeeze + Breakout"),
    ]

    print_table(rows)

    # parameter sweep on mean reversion hold days
    print("\nMean Reversion — max hold day sweep:")
    sweep_rows = []
    for hold in [5, 7, 10, 15, 20]:
        t = strategy_mean_reversion(df, max_hold=hold)
        sweep_rows.append(metrics(t, f"  MR max_hold={hold}"))
    print_table(sweep_rows)

    # parameter sweep on squeeze sensitivity
    print("\nSqueeze+Breakout — width threshold sweep:")
    sq_rows = []
    for w in [0.03, 0.04, 0.05, 0.06]:
        for k in [3, 5, 8]:
            t = strategy_squeeze_breakout(df, squeeze_width=w, min_squeeze_days=k)
            sq_rows.append(metrics(t, f"  squeeze w<{w} k>={k}"))
    print_table(sq_rows)

    trade_detail(mr_trades,  "Mean Reversion")
    trade_detail(bo_trades,  "Breakout")
    trade_detail(sq_trades,  "Squeeze+Breakout")


if __name__ == "__main__":
    main()
