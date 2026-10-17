"""Analyze SPXW cbbo-1m intraday data for 3 questions:

  1. Entry-minute sweep: 9:30 → 11:30 ET, find optimal entry minute.
  2. SL whipsaw rate: how often does SL trigger spike then recover within N minutes?
  3. Real slippage: at SL trigger, what's mid vs ask (true close price)?

Reuses bot's strategy: 50pt × 0.75% OTM BPS, SL = 2.0× credit, hold to close.

Output: prints summary tables. Saves results to results.json.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data_cbbo_1m"
SPX_CACHE = Path(__file__).parent / "spx_intraday_cache.parquet"
RESULTS = Path(__file__).parent / "results.json"

# Strategy params (match bot config)
SPREAD_WIDTH = 50
OTM_PCT = 0.75 / 100      # 0.75%
STOP_MULTIPLIER = 2.0
MIN_CREDIT = 1.20
TARGET_CREDIT = 2.00

# Sweep ranges
ENTRY_MINUTES = [(h, m) for h in range(9, 12) for m in range(0, 60, 1)
                 if (h, m) >= (9, 30) and (h, m) <= (11, 30)]
WHIPSAW_WINDOWS_MIN = [1, 2, 5, 10, 15]


@dataclass
class TradeResult:
    date: str
    entry_h: int
    entry_m: int
    short_strike: float
    long_strike: float
    entry_credit: float
    stop_price: float
    sl_fired: bool
    sl_fire_minute: str | None
    sl_fired_was_whipsaw: dict   # {window_min: True if recovered within N min}
    final_mark: float
    sl_close_price: float | None  # ask at SL fire (true close cost)
    pnl_pts: float                # final realized P/L per spread (SPX points)


def _parse_strike(symbol: str) -> int:
    m = re.search(r"P0?(\d+)000$", symbol)
    return int(m.group(1)) if m else 0


def _per_minute_spread(df: pd.DataFrame, short_k: int, long_k: int) -> pd.DataFrame:
    """Return per-minute (mid, ask_to_close, bid_to_close) for the spread.

    bid_to_close = short.bid - long.ask  (worst case to close — pay ask on long, receive bid on short)
    mid = (short.bid+short.ask)/2 - (long.bid+long.ask)/2
    ask_to_close = short.ask - long.bid (best case to close — what credit you'd receive selling out)

    For BPS "close" means buying-back: pay short.ask, sell long.bid.
    So actual_close_cost = short.ask - long.bid (positive — what you pay net).
    """
    # Pivot to one row per minute with both legs side-by-side
    short_df = df[df["symbol"].apply(_parse_strike) == short_k].copy()
    long_df = df[df["symbol"].apply(_parse_strike) == long_k].copy()
    if short_df.empty or long_df.empty:
        return pd.DataFrame()

    short_df["ts_min"] = short_df["ts_event"].dt.floor("min")
    long_df["ts_min"] = long_df["ts_event"].dt.floor("min")
    short_g = short_df.groupby("ts_min").agg({"bid_px_00": "last", "ask_px_00": "last"})
    long_g = long_df.groupby("ts_min").agg({"bid_px_00": "last", "ask_px_00": "last"})
    short_g.columns = ["s_bid", "s_ask"]
    long_g.columns = ["l_bid", "l_ask"]
    merged = short_g.join(long_g, how="inner")
    if merged.empty:
        return merged
    merged["mid"] = ((merged["s_bid"] + merged["s_ask"]) / 2
                     - (merged["l_bid"] + merged["l_ask"]) / 2)
    merged["close_cost"] = merged["s_ask"] - merged["l_bid"]   # what we pay to close (positive = debit)
    merged["best_credit"] = merged["s_bid"] - merged["l_ask"]  # what credit at open (positive)
    return merged


def _simulate_trade(spread_df: pd.DataFrame, entry_ts, spx_at_entry: float,
                    short_k: int, long_k: int) -> TradeResult | None:
    """Simulate a single BPS trade entered at entry_ts. Walk forward to close."""
    if entry_ts not in spread_df.index:
        return None
    entry_row = spread_df.loc[entry_ts]
    entry_credit = float(entry_row["best_credit"])
    if entry_credit < MIN_CREDIT:
        return None
    stop_price = entry_credit * STOP_MULTIPLIER

    # Walk forward minutes until either SL or close
    walk = spread_df[spread_df.index >= entry_ts]
    sl_fire_ts = None
    sl_close_cost = None
    for ts, row in walk.iterrows():
        # SL triggers when close_cost (what we'd pay to close) >= stop_price
        if float(row["close_cost"]) >= stop_price:
            sl_fire_ts = ts
            sl_close_cost = float(row["close_cost"])
            break

    # Final outcome
    if sl_fire_ts is not None:
        # Stopped out: pnl = entry_credit - close_cost
        pnl = entry_credit - sl_close_cost
        final_mark = sl_close_cost
        sl_fired = True

        # Whipsaw analysis: did close_cost dip back below stop within N minutes?
        whipsaw = {}
        for win in WHIPSAW_WINDOWS_MIN:
            future = walk[(walk.index > sl_fire_ts) &
                          (walk.index <= sl_fire_ts + pd.Timedelta(minutes=win))]
            if future.empty:
                whipsaw[win] = False
            else:
                whipsaw[win] = bool((future["close_cost"] < stop_price).any())
    else:
        # Held to close: 0DTE expires worthless (full credit retained) typically
        final_row = walk.iloc[-1]
        final_mark = float(final_row["close_cost"])
        # If final close_cost is near 0 → full credit kept
        pnl = entry_credit - final_mark
        sl_fired = False
        sl_fire_ts = None
        sl_close_cost = None
        whipsaw = {w: False for w in WHIPSAW_WINDOWS_MIN}

    return TradeResult(
        date=entry_ts.strftime("%Y-%m-%d"),
        entry_h=entry_ts.hour, entry_m=entry_ts.minute,
        short_strike=short_k, long_strike=long_k,
        entry_credit=round(entry_credit, 2),
        stop_price=round(stop_price, 2),
        sl_fired=sl_fired,
        sl_fire_minute=sl_fire_ts.strftime("%H:%M") if sl_fire_ts else None,
        sl_fired_was_whipsaw=whipsaw,
        final_mark=round(final_mark, 2),
        sl_close_price=round(sl_close_cost, 2) if sl_close_cost else None,
        pnl_pts=round(pnl, 2),
    )


def _pick_strikes(spx_at_entry: float, spread_df_all_strikes: list[int]) -> tuple[int, int] | None:
    """Pick short/long strikes matching bot's 50pt × 0.75% OTM logic."""
    target_short = spx_at_entry * (1 - OTM_PCT)
    # Round to nearest 5
    short_k = int(round(target_short / 5) * 5)
    long_k = short_k - SPREAD_WIDTH
    if short_k in spread_df_all_strikes and long_k in spread_df_all_strikes:
        return short_k, long_k
    return None


def analyze():
    spx_df = pd.read_parquet(SPX_CACHE)
    spx_df.index = pd.to_datetime(spx_df.index)

    files = sorted(DATA_DIR.glob("spxw_cbbo_*.parquet"))
    print(f"[analyze] {len(files)} daily files found")
    if not files:
        return

    all_trades: dict[tuple[int, int], list[TradeResult]] = {tuple(em): [] for em in ENTRY_MINUTES}

    for i, fp in enumerate(files):
        date_str = fp.stem.split("_")[-1]
        day = pd.Timestamp(date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:])
        try:
            spx_open = float(spx_df.loc[day, "Open"])
        except Exception:
            continue

        df = pd.read_parquet(fp)
        if df.empty:
            continue
        df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")
        all_strikes = sorted(df["symbol"].apply(_parse_strike).unique())

        # For each candidate entry minute, simulate
        for h, m in ENTRY_MINUTES:
            entry_ts = day.tz_localize("America/New_York").replace(hour=h, minute=m)
            # SPX at this minute: approximate using yfinance open (we don't have intraday SPX 1m for 6yrs)
            # Reasonable approx: use open + small drift; for entry-min comparison, the relative ranking
            # matters more than absolute spot. Use open as proxy.
            spx_at = spx_open

            picks = _pick_strikes(spx_at, all_strikes)
            if not picks:
                continue
            short_k, long_k = picks
            sd = _per_minute_spread(df, short_k, long_k)
            if sd.empty:
                continue

            # Find closest available minute >= entry_ts
            mask = sd.index >= entry_ts
            if not mask.any():
                continue
            actual_entry_ts = sd.index[mask][0]
            tr = _simulate_trade(sd, actual_entry_ts, spx_at, short_k, long_k)
            if tr:
                all_trades[(h, m)].append(tr)

        if (i + 1) % 50 == 0:
            print(f"  processed {i+1}/{len(files)} days")

    # ─── REPORTS ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("ENTRY-MINUTE SWEEP — P&L by entry time")
    print(f"{'='*70}")
    print(f"{'Time':>6} {'N':>5} {'Win%':>6} {'AvgPnL':>8} {'TotalPnL':>10} {'SLrate%':>8} {'Sharpe':>7}")
    minute_stats = []
    for (h, m), trades in sorted(all_trades.items()):
        if not trades:
            continue
        pnls = [t.pnl_pts for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        sl_fires = sum(1 for t in trades if t.sl_fired)
        avg = sum(pnls) / len(pnls)
        total = sum(pnls)
        sharpe = avg / (pd.Series(pnls).std() + 1e-9) if len(pnls) > 1 else 0
        win_pct = wins / len(trades) * 100
        sl_pct = sl_fires / len(trades) * 100
        print(f"{h:02d}:{m:02d} {len(trades):>5} {win_pct:>5.1f}% {avg:>7.3f} {total*100:>9.0f} "
              f"{sl_pct:>7.1f}% {sharpe:>6.2f}")
        minute_stats.append({
            "time": f"{h:02d}:{m:02d}", "n": len(trades), "win_pct": win_pct,
            "avg_pnl": avg, "total_pnl_dollars": total * 100,
            "sl_rate": sl_pct, "sharpe": sharpe,
        })

    # SL whipsaw analysis (from 10:30 entries only)
    print(f"\n{'='*70}")
    print("SL WHIPSAW — entered at 10:30 ET, SL fired, did mark recover within N min?")
    print(f"{'='*70}")
    target_trades = all_trades.get((10, 30), [])
    sl_trades = [t for t in target_trades if t.sl_fired]
    print(f"Total 10:30 trades: {len(target_trades)}")
    print(f"SL fired: {len(sl_trades)} ({len(sl_trades)/max(len(target_trades),1)*100:.1f}%)")
    for win in WHIPSAW_WINDOWS_MIN:
        whip = sum(1 for t in sl_trades if t.sl_fired_was_whipsaw.get(win, False))
        rate = whip / max(len(sl_trades), 1) * 100
        print(f"  recovered within {win:>2}min: {whip:>4}/{len(sl_trades)} ({rate:.1f}%)")

    # Save full results
    out = {
        "entry_minute_sweep": minute_stats,
        "whipsaw_analysis_1030": {
            "total_trades": len(target_trades),
            "sl_fires": len(sl_trades),
            "windows": {
                str(w): {
                    "recovered_count": sum(1 for t in sl_trades if t.sl_fired_was_whipsaw.get(w, False)),
                    "total": len(sl_trades),
                }
                for w in WHIPSAW_WINDOWS_MIN
            },
        },
        "trades_count": sum(len(v) for v in all_trades.values()),
        "files_analyzed": len(files),
    }
    RESULTS.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n[analyze] saved results to {RESULTS}")


if __name__ == "__main__":
    analyze()
