"""
Strategy Explorer — SPX 2013–2026
==================================
Standalone backtests for multiple strategies using SPX/VIX daily data.
No options pricing required. $50/point sizing throughout for fair comparison.

Strategies tested
-----------------
1.  RSI Mean Reversion      — long when RSI(14) < 30, exit at RSI > 50
2.  RSI Overbought Short    — short when RSI(14) > 70, exit at RSI < 50
3.  VIX Spike Reversal      — long SPX when VIX spikes >15% in a day
4.  VIX Crush               — short SPX (or stand aside) when VIX drops >15%
5.  Gap Fade                — fade opening gaps >0.5% back toward prior close
6.  Gap Follow              — trade in direction of gap >0.5%
7.  Golden/Death Cross      — 50/200 MA crossover long/short
8.  MACD Signal Cross       — classic 12/26/9 MACD
9.  Mean Reversion 5-day    — buy after 3+ consecutive down days
10. Trend Continuation      — buy after 3+ consecutive up days
11. Monday Effect           — go long every Monday open, exit Friday close
12. VIX Term Structure      — long when VIX > 20 (elevated fear premium)
13. High Volume Reversal    — fade high-volume down days (capitulation signal)
14. ATR Breakout            — enter when daily range expands > 1.5x ATR(14)

BPS reference: 453 trades | $183,312 | 75.5% win | Sharpe 4.59 | MaxDD -$9,756
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_CSV = Path(__file__).parent / "ten_year" / "spx_vix_daily.csv"
DOLLARS_PER_POINT = 50
BPS_REF = dict(label="── BPS (options baseline) ──", trades=453,
               total_pnl=183312, win_rate=0.755, avg_pnl=405,
               max_dd=-9756, sharpe=4.594)


# ── data + indicators ─────────────────────────────────────────────────────────

def load() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, index_col="date", parse_dates=True)

    c = df["spx_close"]
    # RSI(14)
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # MACD 12/26/9
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # MAs
    df["ma50"]  = c.rolling(50).mean()
    df["ma200"] = c.rolling(200).mean()

    # ATR(14)
    prev = c.shift(1)
    tr   = pd.concat([df["spx_high"] - df["spx_low"],
                      (df["spx_high"] - prev).abs(),
                      (df["spx_low"]  - prev).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # gap %
    df["gap_pct"] = (df["spx_open"] - c.shift(1)) / c.shift(1)

    # VIX daily % change
    df["vix_chg"] = df["vix_close"].pct_change()

    # consecutive up/down days
    df["up_day"]   = (c.diff() > 0).astype(int)
    df["consec_up"]   = df["up_day"].groupby((df["up_day"]   != df["up_day"].shift()).cumsum()).cumcount()
    df["down_day"] = (c.diff() < 0).astype(int)
    df["consec_dn"]   = df["down_day"].groupby((df["down_day"] != df["down_day"].shift()).cumsum()).cumcount()

    # SPY volume z-score (10-day)
    df["vol_z"] = (df["spy_volume"] - df["spy_volume"].rolling(10).mean()) / df["spy_volume"].rolling(10).std()

    return df.dropna(subset=["rsi", "ma200"])


# ── trade engine ──────────────────────────────────────────────────────────────

def run(df: pd.DataFrame,
        entry_fn,
        exit_fn,
        direction_fn=None,
        use_open_entry: bool = False,
        use_open_exit: bool = False,
        max_hold: int = 30) -> list[dict]:
    """
    Generic backtester.
    entry_fn(row, prev_row) -> bool
    exit_fn(row, entry_row, hold_days) -> bool
    direction_fn(row, prev_row) -> 'long' | 'short'  (default: 'long')
    """
    trades = []
    in_trade = False
    entry_px = entry_date = direction = hold = None
    rows = list(df.iterrows())

    for i, (dt, row) in enumerate(rows):
        prev = rows[i - 1][1] if i > 0 else row

        if in_trade:
            hold += 1
            exit_px = row["spx_open"] if use_open_exit else row["spx_close"]
            if exit_fn(row, df.loc[entry_date], hold) or hold >= max_hold:
                mult = 1 if direction == "long" else -1
                pnl  = mult * (exit_px - entry_px) * DOLLARS_PER_POINT
                trades.append(dict(entry=entry_date, exit=dt,
                                   entry_px=entry_px, exit_px=exit_px,
                                   direction=direction, hold=hold, pnl=pnl))
                in_trade = False

        if not in_trade and entry_fn(row, prev):
            entry_px  = row["spx_open"] if use_open_entry else row["spx_close"]
            entry_date = dt
            direction  = (direction_fn(row, prev) if direction_fn else "long")
            hold       = 0
            in_trade   = True

    return trades


# ── metrics ───────────────────────────────────────────────────────────────────

def metrics(trades: list[dict], label: str) -> dict:
    if not trades:
        return dict(label=label, trades=0, total_pnl=0, win_rate=0,
                    avg_pnl=0, max_dd=0, sharpe=0)
    pnls = np.array([t["pnl"] for t in trades])
    cum  = np.cumsum(pnls)
    dd   = float((cum - np.maximum.accumulate(cum)).min())
    avg  = float(pnls.mean())
    std  = float(pnls.std(ddof=1)) if len(pnls) > 1 else 1.0
    return dict(
        label=label,
        trades=len(trades),
        total_pnl=round(float(pnls.sum()), 0),
        win_rate=round(float((pnls > 0).mean()), 3),
        avg_pnl=round(avg, 0),
        max_dd=round(dd, 0),
        sharpe=round((avg / std) * np.sqrt(252) if std > 0 else 0, 3),
    )


# ── print ─────────────────────────────────────────────────────────────────────

def print_table(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'═'*90}")
        print(f"  {title}")
        print(f"{'═'*90}")
    hdr = f"{'Strategy':<42} {'Trades':>7} {'Total $':>10} {'Win%':>6} {'Avg $':>8} {'MaxDD':>10} {'Sharpe':>7}"
    sep = "-" * len(hdr)
    print(sep)
    print(hdr)
    print(sep)
    for r in rows:
        print(f"{r['label']:<42} {r['trades']:>7} "
              f"${r['total_pnl']:>9,.0f} {r['win_rate']*100:>5.1f}% "
              f"${r['avg_pnl']:>7,.0f} ${r['max_dd']:>9,.0f} {r['sharpe']:>7.3f}")
    print(sep)


# ── strategy definitions ──────────────────────────────────────────────────────

def strat_rsi_long(df):
    return run(df,
        entry_fn=lambda r, p: r["rsi"] < 30,
        exit_fn =lambda r, e, h: r["rsi"] > 50,
        max_hold=20)

def strat_rsi_short(df):
    return run(df,
        entry_fn =lambda r, p: r["rsi"] > 70,
        exit_fn  =lambda r, e, h: r["rsi"] < 50,
        direction_fn=lambda r, p: "short",
        max_hold=20)

def strat_vix_spike_reversal(df, threshold=0.15):
    return run(df,
        entry_fn=lambda r, p: r["vix_chg"] > threshold,
        exit_fn =lambda r, e, h: h >= 5 or r["spx_close"] < e["spx_close"] * 0.97,
        use_open_entry=True,
        max_hold=10)

def strat_gap_fade(df, min_gap=0.005):
    """Fade the opening gap — enter at open, exit when price returns to prev close."""
    trades = []
    rows = list(df.iterrows())
    for i, (dt, row) in enumerate(rows[1:], 1):
        prev_close = rows[i-1][1]["spx_close"]
        gap = row["gap_pct"]
        if abs(gap) < min_gap:
            continue
        entry_px = row["spx_open"]
        direction = "short" if gap > 0 else "long"
        # exit: price crosses back through prev_close, or end of day
        exit_px = row["spx_close"]
        if direction == "short":
            exit_px = min(row["spx_close"], prev_close) if row["spx_low"] <= prev_close else row["spx_close"]
        else:
            exit_px = max(row["spx_close"], prev_close) if row["spx_high"] >= prev_close else row["spx_close"]
        mult = 1 if direction == "long" else -1
        pnl  = mult * (exit_px - entry_px) * DOLLARS_PER_POINT
        trades.append(dict(entry=dt, exit=dt, direction=direction,
                           entry_px=entry_px, exit_px=exit_px, hold=1, pnl=pnl))
    return trades

def strat_gap_follow(df, min_gap=0.005):
    """Trade in the direction of the gap."""
    trades = []
    rows = list(df.iterrows())
    for i, (dt, row) in enumerate(rows[1:], 1):
        gap = row["gap_pct"]
        if abs(gap) < min_gap:
            continue
        direction = "long" if gap > 0 else "short"
        entry_px  = row["spx_open"]
        exit_px   = row["spx_close"]
        mult = 1 if direction == "long" else -1
        pnl  = mult * (exit_px - entry_px) * DOLLARS_PER_POINT
        trades.append(dict(entry=dt, exit=dt, direction=direction,
                           entry_px=entry_px, exit_px=exit_px, hold=1, pnl=pnl))
    return trades

def strat_golden_cross(df):
    """Long when 50MA crosses above 200MA, short when crosses below."""
    trades = []
    rows = list(df.iterrows())
    position = None
    entry_px = entry_date = direction = None

    for i, (dt, row) in enumerate(rows[1:], 1):
        prev = rows[i-1][1]
        cross_up   = prev["ma50"] <= prev["ma200"] and row["ma50"] > row["ma200"]
        cross_down = prev["ma50"] >= prev["ma200"] and row["ma50"] < row["ma200"]

        if position is not None:
            if (direction == "long" and cross_down) or (direction == "short" and cross_up):
                mult = 1 if direction == "long" else -1
                pnl  = mult * (row["spx_close"] - entry_px) * DOLLARS_PER_POINT
                trades.append(dict(entry=entry_date, exit=dt, direction=direction,
                                   entry_px=entry_px, exit_px=row["spx_close"],
                                   hold=(dt - entry_date).days, pnl=pnl))
                position = None

        if position is None:
            if cross_up:
                position, direction, entry_px, entry_date = "long", "long", row["spx_close"], dt
            elif cross_down:
                position, direction, entry_px, entry_date = "short", "short", row["spx_close"], dt

    return trades

def strat_macd(df):
    return run(df,
        entry_fn   =lambda r, p: p["macd"] <= p["macd_signal"] and r["macd"] > r["macd_signal"],
        exit_fn    =lambda r, e, h: r["macd"] < r["macd_signal"],
        direction_fn=lambda r, p: "long",
        max_hold=30)

def strat_consec_down_reversal(df, n=3):
    """Buy after N consecutive down days."""
    return run(df,
        entry_fn=lambda r, p: r["consec_dn"] >= n,
        exit_fn =lambda r, e, h: h >= 5 or r["spx_close"] > e["spx_close"] * 1.02,
        max_hold=10)

def strat_consec_up_continuation(df, n=3):
    """Buy after N consecutive up days (trend continuation)."""
    return run(df,
        entry_fn=lambda r, p: r["consec_up"] >= n,
        exit_fn =lambda r, e, h: h >= 5,
        max_hold=5)

def strat_monday_effect(df):
    """Long every Monday open, exit Friday close."""
    trades = []
    rows = list(df.iterrows())
    in_trade = False
    entry_px = entry_date = None

    for dt, row in rows:
        dow = dt.dayofweek  # 0=Mon, 4=Fri
        if not in_trade and dow == 0:
            entry_px   = row["spx_open"]
            entry_date = dt
            in_trade   = True
        elif in_trade and dow == 4:
            pnl = (row["spx_close"] - entry_px) * DOLLARS_PER_POINT
            trades.append(dict(entry=entry_date, exit=dt, direction="long",
                               entry_px=entry_px, exit_px=row["spx_close"], hold=4, pnl=pnl))
            in_trade = False
    return trades

def strat_vix_elevated(df, threshold=20.0):
    """Sell premium (short SPX proxy) when VIX > threshold — captures fear premium."""
    return run(df,
        entry_fn   =lambda r, p: r["vix_close"] > threshold and p["vix_close"] <= threshold,
        exit_fn    =lambda r, e, h: r["vix_close"] <= threshold or h >= 5,
        direction_fn=lambda r, p: "short",
        max_hold=5)

def strat_high_vol_reversal(df, z_thresh=2.0):
    """Buy after high-volume sell-off day (capitulation)."""
    return run(df,
        entry_fn=lambda r, p: r["vol_z"] > z_thresh and r["spx_close"] < r["spx_open"],
        exit_fn =lambda r, e, h: h >= 5 or r["spx_close"] > e["spx_close"] * 1.02,
        use_open_entry=True,
        max_hold=10)

def strat_atr_breakout(df, mult=1.5):
    """Enter when daily range > 1.5x ATR (volatility expansion day), fade it."""
    return run(df,
        entry_fn=lambda r, p: (r["spx_high"] - r["spx_low"]) > mult * p["atr14"]
                               and r["spx_close"] < r["spx_open"],
        exit_fn =lambda r, e, h: h >= 3,
        max_hold=3)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load()
    print(f"Data: {df.index[0].date()} → {df.index[-1].date()}  ({len(df)} bars)\n")

    strategies = [
        ("RSI Mean Rev — Long (RSI<30)",      strat_rsi_long(df)),
        ("RSI Overbought — Short (RSI>70)",   strat_rsi_short(df)),
        ("VIX Spike Reversal (>15%)",         strat_vix_spike_reversal(df)),
        ("Gap Fade (>0.5%)",                  strat_gap_fade(df)),
        ("Gap Follow (>0.5%)",                strat_gap_follow(df)),
        ("Golden/Death Cross (50/200 MA)",    strat_golden_cross(df)),
        ("MACD Signal Cross",                 strat_macd(df)),
        ("3-Day Consec Down Reversal",        strat_consec_down_reversal(df, 3)),
        ("3-Day Consec Up Continuation",      strat_consec_up_continuation(df, 3)),
        ("Monday-to-Friday Long",             strat_monday_effect(df)),
        ("VIX Elevated Short (VIX>20 enter)", strat_vix_elevated(df, 20)),
        ("High-Volume Reversal",              strat_high_vol_reversal(df)),
        ("ATR Expansion Fade",                strat_atr_breakout(df)),
    ]

    rows = [BPS_REF]
    for label, trades in strategies:
        rows.append(metrics(trades, label))

    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    print_table(rows, "ALL STRATEGIES — sorted by Sharpe  (SPX directional, $50/pt)")

    # highlight anything that beats BPS on Sharpe
    print("\n★  Strategies that beat BPS Sharpe (4.594):")
    winners = [r for r in rows if r["label"] != BPS_REF["label"] and r["sharpe"] > 4.594]
    if winners:
        for w in winners:
            print(f"   {w['label']:<42}  Sharpe {w['sharpe']:.3f}  |  ${w['total_pnl']:,.0f}  |  {w['win_rate']*100:.1f}%")
    else:
        print("   None beat BPS on Sharpe. BPS remains the strongest.")

    # deep-dive on top 3 non-BPS by sharpe
    ranked = [r for r in rows if r["label"] != BPS_REF["label"]][:3]
    print(f"\nTop 3 non-BPS strategies:")
    for r in ranked:
        print(f"  {r['label']}: Sharpe {r['sharpe']:.3f}, ${r['total_pnl']:,.0f} total, {r['win_rate']*100:.1f}% win, MaxDD ${r['max_dd']:,.0f}")


if __name__ == "__main__":
    main()
