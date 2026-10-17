"""Clean 2025 backtest of the live bot's EXACT config.

NO hindsight bias. NO inflated entry credit. NO SPX-low SL trigger.

Methodology (matches live bot exactly):
  - 1 contract × 50pt wide × 1% OTM seed
  - Credit-seeking strike selection (scan 5 nearest, pick credit closest to $2)
  - Min credit $1.20 (else skip)
  - 10:30 ET entry
  - Direction filter: SPX_at_1030 < SPX_at_open → skip
  - VIX gate: VIX > 25 → skip
  - 1 trade per day max
  - SL trigger: spread mid >= entry_credit × stop_multiplier
  - SL exit price: real close cost (ask_short - bid_long) at trigger time
  - Held-to-close otherwise: final mid at 16:00 ET

Outputs both gross (mid-fill) and net (real-slippage) P&L for SL multipliers 2.0 and 3.0.

DATA SOURCE: spxw_p_cbbo_1m + spxw_c_cbbo_1m (cbbo-1m bid/ask snapshots per minute).
SPX at 10:30: derived from put-call parity at the entry minute (most accurate).
SPX at open: yfinance daily Open (proxy for 9:30 ET).
VIX: yfinance daily Close of prior day (gate uses prev close).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ─── Paths ───────────────────────────────────────────────────────────────────
DATA_PUTS = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
DATA_CALLS = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_c_cbbo_1m"
OUT_DIR = Path(__file__).parent
OUT_DIR.mkdir(exist_ok=True)
import sys
_YEAR = sys.argv[1] if len(sys.argv) > 1 else "2025"
_NO_DIR_FILTER = "--no-dir-filter" in sys.argv
_suffix = "_nodir" if _NO_DIR_FILTER else ""
RESULT_PATH = OUT_DIR / f"result_{_YEAR}{_suffix}.json"

# ─── Strategy params (match webull_bot/config.yaml) ──────────────────────────
SPREAD_WIDTH = 50
OTM_PCT_SEED = 0.010          # 1% OTM seed
TARGET_CREDIT = 2.00
MIN_CREDIT = 1.20
STOP_MULTIPLIERS = [2.0, 3.0]
VIX_MAX = 25.0
ENTRY_H, ENTRY_M = 10, 30
EXIT_H, EXIT_M = 16, 0

# ─── Helpers ─────────────────────────────────────────────────────────────────
_RX = re.compile(r"[CP]0?(\d+)000$")


def _strike(sym: str) -> int:
    m = _RX.search(sym)
    return int(m.group(1)) if m else 0


def _load_minute_quotes(parquet_path: Path) -> pd.DataFrame:
    """Return DataFrame indexed by (ts_min, strike), columns bid/ask. Last quote per minute."""
    df = pd.read_parquet(parquet_path)
    if df.empty:
        return pd.DataFrame()
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
    if df.empty:
        return pd.DataFrame()
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_strike)
    g = df.sort_values("ts_event").groupby(["ts_min", "strike"]).last()
    return g[["bid_px_00", "ask_px_00"]]


def _spx_from_parity(call_df: pd.DataFrame, put_df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Put-call parity: SPX ≈ C - P + K (ignoring r for 0DTE).
    Picks strike where |C-P| is smallest (closest to ATM)."""
    try:
        c_min = call_df.xs(ts, level="ts_min")
        p_min = put_df.xs(ts, level="ts_min")
    except KeyError:
        return None
    if c_min.empty or p_min.empty:
        return None
    c_mid = (c_min["bid_px_00"] + c_min["ask_px_00"]) / 2
    p_mid = (p_min["bid_px_00"] + p_min["ask_px_00"]) / 2
    merged = pd.DataFrame({"c": c_mid, "p": p_mid}).dropna()
    if merged.empty:
        return None
    merged["diff"] = (merged["c"] - merged["p"]).abs()
    best = merged.sort_values("diff").iloc[0]
    return float(best.name) + float(best["c"]) - float(best["p"])


def _credit_seek(put_quotes_at_ts: pd.DataFrame, spx_at_entry: float) -> tuple[int, int, float, float] | None:
    """Mimics bot's find_best_spread:
       - target_short = round(spx*(1-0.01)/5)*5
       - scan 5 nearest strikes
       - for each, long = short - 50
       - pick credit closest to $2.00 (target), >= $1.20 (min)
       Returns (short, long, credit_mid, credit_open_bid) or None.
    """
    target_short = int(round(spx_at_entry * (1 - OTM_PCT_SEED) / 5) * 5)
    all_strikes = sorted(put_quotes_at_ts.index.tolist())
    if not all_strikes:
        return None
    candidates = sorted(all_strikes, key=lambda s: abs(s - target_short))[:5]
    best = None
    best_diff = float("inf")
    for short_k in candidates:
        long_k = short_k - SPREAD_WIDTH
        if short_k not in put_quotes_at_ts.index or long_k not in put_quotes_at_ts.index:
            continue
        s = put_quotes_at_ts.loc[short_k]
        l = put_quotes_at_ts.loc[long_k]
        s_mid = (s["bid_px_00"] + s["ask_px_00"]) / 2
        l_mid = (l["bid_px_00"] + l["ask_px_00"]) / 2
        credit_mid = s_mid - l_mid
        # Open credit (worst case to fill = short.bid - long.ask)
        credit_open_bid = s["bid_px_00"] - l["ask_px_00"]
        if credit_mid < MIN_CREDIT:
            continue
        diff = abs(credit_mid - TARGET_CREDIT)
        if diff < best_diff:
            best_diff = diff
            best = (int(short_k), int(long_k), float(credit_mid), float(credit_open_bid))
    return best


def _spread_series(put_quotes: pd.DataFrame, short_k: int, long_k: int) -> pd.DataFrame:
    """Per-minute spread: mid, close_cost (ask_short - bid_long = real exit), open_credit (bid_short - ask_long = real entry)."""
    try:
        s = put_quotes.xs(short_k, level="strike")
        l = put_quotes.xs(long_k, level="strike")
    except KeyError:
        return pd.DataFrame()
    m = s.join(l, lsuffix="_s", rsuffix="_l", how="inner")
    if m.empty:
        return m
    m["mid"] = ((m["bid_px_00_s"] + m["ask_px_00_s"]) / 2 - (m["bid_px_00_l"] + m["ask_px_00_l"]) / 2)
    m["close_cost"] = m["ask_px_00_s"] - m["bid_px_00_l"]
    m["open_credit"] = m["bid_px_00_s"] - m["ask_px_00_l"]
    return m[["mid", "close_cost", "open_credit"]]


def _simulate_trade(spread: pd.DataFrame, entry_ts: pd.Timestamp, entry_credit_mid: float,
                    stop_mult: float) -> dict | None:
    """Walk forward minute by minute. SL triggers when mid >= entry_credit * stop_mult.
    Returns dict with both mid-based and real-close pnl."""
    stop_price = entry_credit_mid * stop_mult
    walk = spread[spread.index >= entry_ts]
    if walk.empty:
        return None
    sl_fire_ts = None
    sl_mid = None
    sl_close_cost = None
    for ts, row in walk.iterrows():
        if float(row["mid"]) >= stop_price:
            sl_fire_ts = ts
            sl_mid = float(row["mid"])
            sl_close_cost = float(row["close_cost"])
            break
    if sl_fire_ts is not None:
        return {
            "sl_fired": True,
            "sl_fire_ts": str(sl_fire_ts),
            "entry_credit": entry_credit_mid,
            "stop_price": stop_price,
            "exit_mid": sl_mid,
            "exit_close_cost": sl_close_cost,
            "pnl_pts_mid": entry_credit_mid - sl_mid,
            "pnl_pts_real": entry_credit_mid - sl_close_cost,
        }
    # Held to close
    final = walk.iloc[-1]
    return {
        "sl_fired": False,
        "sl_fire_ts": None,
        "entry_credit": entry_credit_mid,
        "stop_price": stop_price,
        "exit_mid": float(final["mid"]),
        "exit_close_cost": float(final["close_cost"]),
        "pnl_pts_mid": entry_credit_mid - float(final["mid"]),
        "pnl_pts_real": entry_credit_mid - float(final["close_cost"]),
    }


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    print(f"[clean-{_YEAR}] loading SPX and VIX daily history from yfinance...")
    yr_start = f"{int(_YEAR)-1}-12-15"
    yr_end = f"{int(_YEAR)+1}-01-05" if _YEAR != "2026" else "2026-05-23"
    spx_df = yf.Ticker("^GSPC").history(start=yr_start, end=yr_end)
    spx_df.index = pd.to_datetime(spx_df.index).tz_localize(None) if spx_df.index.tz else spx_df.index
    vix_df = yf.Ticker("^VIX").history(start=yr_start, end=yr_end)
    vix_df.index = pd.to_datetime(vix_df.index).tz_localize(None) if vix_df.index.tz else vix_df.index
    print(f"   SPX {len(spx_df)} days  VIX {len(vix_df)} days")

    files = sorted(DATA_PUTS.glob(f"spxw_p_cbbo_{_YEAR}*.parquet"))
    print(f"[clean-{_YEAR}] {len(files)} put files for {_YEAR}")

    trades_by_mult = {m: [] for m in STOP_MULTIPLIERS}
    skipped = {"vix_gate": 0, "direction_filter": 0, "no_entry_quote": 0,
               "no_qualifying_spread": 0, "no_call_data": 0, "no_parity": 0}
    days_attempted = 0

    for i, p_fp in enumerate(files):
        date_str = p_fp.stem.split("_")[-1]
        day = pd.Timestamp(date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:])

        # SPX/VIX daily lookups
        try:
            spx_open = float(spx_df.loc[day, "Open"])
        except (KeyError, IndexError):
            continue

        # VIX gate (use PRIOR day's close as proxy for "at entry")
        try:
            prior_vix = vix_df.loc[:day].iloc[-2]["Close"] if day in vix_df.index else vix_df.loc[:day].iloc[-1]["Close"]
        except (IndexError, KeyError):
            prior_vix = 20.0
        if prior_vix >= VIX_MAX:
            skipped["vix_gate"] += 1
            continue

        # Load call file (needed for SPX at 10:30 via parity)
        c_fp = DATA_CALLS / p_fp.name.replace("_p_", "_c_")
        if not c_fp.exists():
            skipped["no_call_data"] += 1
            continue

        put_quotes = _load_minute_quotes(p_fp)
        if put_quotes.empty:
            continue
        call_quotes = _load_minute_quotes(c_fp)

        entry_ts = day.tz_localize("America/New_York").replace(hour=ENTRY_H, minute=ENTRY_M)

        # Compute SPX at 10:30 via put-call parity
        spx_at_1030 = _spx_from_parity(call_quotes, put_quotes, entry_ts)
        if spx_at_1030 is None:
            skipped["no_parity"] += 1
            continue

        # DIRECTION FILTER (matches live bot — uses live SPX vs daily open)
        if not _NO_DIR_FILTER and spx_at_1030 < spx_open:
            skipped["direction_filter"] += 1
            continue

        days_attempted += 1

        # Pick strikes via credit-seek (matches bot's find_best_spread)
        try:
            entry_quotes = put_quotes.xs(entry_ts, level="ts_min")
        except KeyError:
            skipped["no_entry_quote"] += 1
            continue
        pick = _credit_seek(entry_quotes, spx_at_1030)
        if pick is None:
            skipped["no_qualifying_spread"] += 1
            continue
        short_k, long_k, credit_mid, credit_open_bid = pick

        # Build spread series and simulate for each stop multiplier
        spread = _spread_series(put_quotes, short_k, long_k)
        if spread.empty:
            continue

        for mult in STOP_MULTIPLIERS:
            sim = _simulate_trade(spread, entry_ts, credit_mid, mult)
            if sim is None:
                continue
            sim["day"] = str(day.date())
            sim["short_strike"] = short_k
            sim["long_strike"] = long_k
            sim["spx_at_open"] = spx_open
            sim["spx_at_1030"] = spx_at_1030
            sim["vix_prior_close"] = float(prior_vix)
            sim["credit_open_bid"] = credit_open_bid  # for slippage analysis
            trades_by_mult[mult].append(sim)

        if (i + 1) % 50 == 0:
            print(f"  processed {i+1}/{len(files)}, attempted={days_attempted}, trades@2.0x={len(trades_by_mult[2.0])}")

    # ─── REPORTS ─────────────────────────────────────────────────────────────
    print(f"\n[clean-{_YEAR}] DONE — {_YEAR} calendar year")
    print(f"  Files processed: {len(files)}")
    print(f"  Days attempted (after VIX+direction filters): {days_attempted}")
    print(f"  Skipped: {skipped}")
    print()

    out = {"year": 2025, "files_total": len(files), "days_attempted": days_attempted,
           "skipped": skipped, "by_stop_multiplier": {}}

    for mult in STOP_MULTIPLIERS:
        ts = trades_by_mult[mult]
        if not ts:
            print(f"  STOP {mult}x: no trades")
            continue
        n = len(ts)
        wins_mid = sum(1 for t in ts if t["pnl_pts_mid"] > 0)
        wins_real = sum(1 for t in ts if t["pnl_pts_real"] > 0)
        sl_fires = sum(1 for t in ts if t["sl_fired"])
        pnl_mid = sum(t["pnl_pts_mid"] * 100 for t in ts)
        pnl_real = sum(t["pnl_pts_real"] * 100 for t in ts)
        avg_mid = pnl_mid / n
        avg_real = pnl_real / n
        avg_credit = np.mean([t["entry_credit"] for t in ts])
        # Worst day
        worst_mid = min(t["pnl_pts_mid"] * 100 for t in ts)
        worst_real = min(t["pnl_pts_real"] * 100 for t in ts)

        # Sharpe (per-trade, annualized to 250 trading days)
        pnls_real = [t["pnl_pts_real"] * 100 for t in ts]
        sharpe = avg_real / (np.std(pnls_real) + 1e-9) * np.sqrt(250) if len(pnls_real) > 1 else 0

        # Max drawdown (cumulative)
        cum = np.cumsum(pnls_real)
        peak = np.maximum.accumulate(cum)
        max_dd = float((cum - peak).min())

        block = {
            "trades": n,
            "wins_mid": wins_mid,
            "wins_real": wins_real,
            "win_pct_mid": round(wins_mid / n * 100, 1),
            "win_pct_real": round(wins_real / n * 100, 1),
            "sl_fires": sl_fires,
            "sl_pct": round(sl_fires / n * 100, 1),
            "avg_credit": round(avg_credit, 2),
            "total_pnl_mid_USD": round(pnl_mid, 0),
            "total_pnl_real_USD": round(pnl_real, 0),
            "avg_pnl_per_trade_mid_USD": round(avg_mid, 2),
            "avg_pnl_per_trade_real_USD": round(avg_real, 2),
            "worst_day_mid_USD": round(worst_mid, 0),
            "worst_day_real_USD": round(worst_real, 0),
            "max_drawdown_USD": round(max_dd, 0),
            "sharpe": round(sharpe, 2),
            "slippage_drag_USD": round(pnl_mid - pnl_real, 0),
        }
        out["by_stop_multiplier"][str(mult)] = block

        print(f"  STOP {mult}× SL:")
        print(f"    trades:           {n}")
        print(f"    win rate (mid):   {wins_mid}/{n} = {wins_mid/n*100:.1f}%")
        print(f"    win rate (real):  {wins_real}/{n} = {wins_real/n*100:.1f}%")
        print(f"    SL fires:         {sl_fires}/{n} = {sl_fires/n*100:.1f}%")
        print(f"    avg credit:       ${avg_credit:.2f}")
        print(f"    avg pnl/trade:    ${avg_mid:+.0f} mid  /  ${avg_real:+.0f} real")
        print(f"    TOTAL 2025 P&L:   ${pnl_mid:+,.0f} mid  /  ${pnl_real:+,.0f} real-slippage")
        print(f"    worst day:        ${worst_mid:,.0f} mid  /  ${worst_real:,.0f} real")
        print(f"    max drawdown:     ${max_dd:,.0f}")
        print(f"    Sharpe (annual):  {sharpe:.2f}")
        print(f"    slippage drag:    ${pnl_mid - pnl_real:,.0f}")
        print()

    RESULT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"[clean-2025] saved {RESULT_PATH.name}")


if __name__ == "__main__":
    main()
