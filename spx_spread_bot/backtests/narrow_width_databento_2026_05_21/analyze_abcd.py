"""ABCD analyzer pass on SPXW cbbo-1m pull.

A) OTM sweep — find optimal % OTM (vs current fixed 0.75%)
B) Delta-based strike selection via Black-Scholes IV inversion
C) True close-cost (ask - bid) vs assumed mid — real slippage
D) Position sizing rules — flat / Kelly / VIX-cond / anti-martingale / risk-budget

Single pass loads each day's parquet, computes a wide grid of trades, then
runs all 4 reports off that grid. Output: results_abcd.json + console tables.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "spxw_p_cbbo_1m"   # new wider-band SPXW puts
CALL_DIR = Path(__file__).parent / "spxw_c_cbbo_1m"   # SPXW calls (for put-call parity → SPX@T)
SPX_CACHE = Path(__file__).parent / "spot_cache_GSPC.parquet"
RESULTS = Path(__file__).parent / "results_abcd.json"
GLOB_PATTERN = "spxw_p_cbbo_*.parquet"
# Filters matching live bot behavior
DIRECTION_FILTER = True   # skip if SPX@1030 < SPX@open
VIX_GATE_MAX = 25.0       # skip if daily VIX close >= this

SPREAD_WIDTH = 50
STOP_MULTIPLIER = 2.0
MIN_CREDIT = 1.20
ENTRY_H, ENTRY_M = 10, 30          # fixed entry 10:30 ET
OTM_SWEEP = [0.40, 0.50, 0.60, 0.65, 0.70, 0.75, 0.85, 1.00]   # %
DELTA_TARGETS = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20]


# ──────────────────────────────────────────────────────────────────────────────
# Black-Scholes utilities (for delta + IV inversion)
# ──────────────────────────────────────────────────────────────────────────────
SQRT_2PI = math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _bs_put(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    """Return (put_price, put_delta). T in years, sigma annualized."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(K - S, 0.0)
        return intrinsic, -1.0 if K > S else 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    delta = -_norm_cdf(-d1)  # put delta in [-1, 0]
    return price, delta


def _implied_vol(market_price: float, S: float, K: float, T: float, r: float) -> float | None:
    """Bisect IV for put. Returns None if can't solve."""
    if market_price <= 0 or T <= 0:
        return None
    intrinsic = max(K - S, 0) * math.exp(-r * T)
    if market_price < intrinsic - 0.01:
        return None
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        p, _ = _bs_put(S, K, T, r, mid)
        if p < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-5:
            break
    return mid


# ──────────────────────────────────────────────────────────────────────────────
# Per-day spread computation
# ──────────────────────────────────────────────────────────────────────────────
_RX_STRIKE = re.compile(r"[CP]0?(\d+)000$")  # match both calls and puts


def _parse_strike(symbol: str) -> int:
    m = _RX_STRIKE.search(symbol)
    return int(m.group(1)) if m else 0


def _spx_from_parity(call_df: pd.DataFrame, put_df: pd.DataFrame, ts) -> float | None:
    """Estimate SPX at a given minute from put-call parity.
    SPX = C - P + K  (ignoring r since 0DTE T≈hours).
    Uses the ATM-ish strike where BOTH call and put have valid bid/ask.
    """
    if call_df.empty or put_df.empty: return None
    # Filter to the given minute and only strike levels with both legs quoted
    for df in (call_df, put_df):
        if "ts_min" not in df.columns:
            df["ts_min"] = df["ts_event"].dt.floor("min")
            df["strike"] = df["symbol"].apply(_parse_strike)
    c = call_df[call_df["ts_min"] == ts].copy()
    p = put_df[put_df["ts_min"] == ts].copy()
    if c.empty or p.empty: return None
    c["mid"] = (c["bid_px_00"] + c["ask_px_00"]) / 2
    p["mid"] = (p["bid_px_00"] + p["ask_px_00"]) / 2
    merged = c.merge(p, on="strike", suffixes=("_c", "_p"))
    if merged.empty: return None
    # Pick the strike with smallest |C - P| (closest to ATM, where parity is most accurate)
    merged["abs_diff"] = (merged["mid_c"] - merged["mid_p"]).abs()
    best = merged.loc[merged["abs_diff"].idxmin()]
    return float(best["strike"] + best["mid_c"] - best["mid_p"])


def _day_per_minute_per_strike(df: pd.DataFrame) -> pd.DataFrame:
    """Index by (minute, strike) → bid/ask. Last valid quote per minute per strike."""
    df = df.copy()
    # Drop rows with NaN or non-positive bid/ask
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
    if df.empty:
        return pd.DataFrame()
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_parse_strike)
    g = df.sort_values("ts_event").groupby(["ts_min", "strike"]).last()
    return g[["bid_px_00", "ask_px_00"]]


def _spread_series(per_min_per_strike: pd.DataFrame, short_k: int, long_k: int) -> pd.DataFrame:
    """Per-minute spread: mid, close_cost (ask-bid debit to close), open_credit (bid-ask credit to sell)."""
    try:
        s = per_min_per_strike.xs(short_k, level="strike")
        l = per_min_per_strike.xs(long_k, level="strike")
    except KeyError:
        return pd.DataFrame()
    m = s.join(l, lsuffix="_s", rsuffix="_l", how="inner")
    if m.empty:
        return m
    m["mid"] = (m["bid_px_00_s"] + m["ask_px_00_s"]) / 2 - (m["bid_px_00_l"] + m["ask_px_00_l"]) / 2
    m["close_cost"] = m["ask_px_00_s"] - m["bid_px_00_l"]  # buy back short, sell long
    m["open_credit"] = m["bid_px_00_s"] - m["ask_px_00_l"]  # sell short, buy long
    return m[["mid", "close_cost", "open_credit"]]


def _simulate(spread: pd.DataFrame, entry_ts, entry_credit_basis: str = "mid") -> dict | None:
    """Simulate BPS held from entry_ts to either SL or session close.

    Matches the LIVE bot's behavior:
      - Entry credit = MID (bot places LIMIT at mid, typically fills there)
      - SL trigger when MID (the streamed mark) reaches stop_price = entry × 2.0
      - Exit price for SL P&L = MID (the model price), and separately we record
        close_cost (ask - bid worst case) to measure slippage
      - Expiry: held to 16:00 ET, P&L = entry - final_mid
    """
    if entry_ts not in spread.index:
        return None
    row = spread.loc[entry_ts]
    entry = float(row["mid"])  # bot fills at mid via LIMIT
    if entry < MIN_CREDIT:
        return None
    stop = entry * STOP_MULTIPLIER
    walk = spread[spread.index >= entry_ts]
    sl_fire = None
    sl_close_cost = None
    sl_mid_at_fire = None
    for ts, r in walk.iterrows():
        # SL trigger when MID crosses stop (matches IBKR mark in monitor.py)
        if float(r["mid"]) >= stop:
            sl_fire = ts
            sl_close_cost = float(r["close_cost"])
            sl_mid_at_fire = float(r["mid"])
            break
    if sl_fire is not None:
        pnl_using_mid = entry - sl_mid_at_fire
        pnl_using_real = entry - sl_close_cost
        return dict(entry_credit=entry, stop_price=stop, sl_fired=True,
                    sl_fire_ts=sl_fire.isoformat(),
                    pnl_pts_assumed_mid=pnl_using_mid,
                    pnl_pts_real_close=pnl_using_real,
                    sl_close_cost=sl_close_cost,
                    sl_mid_at_fire=sl_mid_at_fire)
    final = walk.iloc[-1]
    pnl_using_mid = entry - float(final["mid"])
    pnl_using_real = entry - float(final["close_cost"])
    return dict(entry_credit=entry, stop_price=stop, sl_fired=False,
                sl_fire_ts=None,
                pnl_pts_assumed_mid=pnl_using_mid,
                pnl_pts_real_close=pnl_using_real,
                sl_close_cost=float(final["close_cost"]),
                sl_mid_at_fire=float(final["mid"]))


# ──────────────────────────────────────────────────────────────────────────────
# Main analyzer
# ──────────────────────────────────────────────────────────────────────────────
def run():
    spx_df = pd.read_parquet(SPX_CACHE)
    spx_df.index = pd.to_datetime(spx_df.index)

    # Load VIX daily for VIX gate
    try:
        import yfinance as yf
        vix_df = yf.Ticker("^VIX").history(start="2013-01-01", end="2026-05-23")
        vix_df.index = vix_df.index.tz_localize(None) if vix_df.index.tz else vix_df.index
        vix_close = vix_df["Close"].to_dict()
    except Exception as e:
        print(f"[analyze] VIX load failed, gate disabled: {e}")
        vix_close = {}

    files = sorted(DATA_DIR.glob(GLOB_PATTERN))
    print(f"[analyze] {len(files)} daily files from {DATA_DIR}")
    print(f"[analyze] filters: direction={DIRECTION_FILTER}  vix<={VIX_GATE_MAX}")

    # Storage: for each (config_label, day) → trade result
    otm_trades: dict[float, list[dict]] = {p: [] for p in OTM_SWEEP}
    delta_trades: dict[float, list[dict]] = {d: [] for d in DELTA_TARGETS}
    bot_actual_trades: list[dict] = []  # mimics live bot's credit-seeking logic

    skipped_no_quote = 0
    skipped_no_credit = 0
    skipped_direction = 0
    skipped_vix = 0
    processed = 0

    for i, fp in enumerate(files):
        date_str = fp.stem.split("_")[-1]
        day = pd.Timestamp(date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:])
        try:
            spot = float(spx_df.loc[day, "Open"])
        except Exception:
            continue

        # VIX gate
        if VIX_GATE_MAX:
            vix_val = vix_close.get(day - pd.Timedelta(days=1), vix_close.get(day, None))
            if vix_val is not None and vix_val >= VIX_GATE_MAX:
                skipped_vix += 1
                continue

        df = pd.read_parquet(fp)
        if df.empty:
            continue
        df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")

        # Load call file for put-call parity → SPX@1030 for direction filter
        spx_at_1030 = spot  # fallback to open
        if DIRECTION_FILTER:
            call_fp = CALL_DIR / fp.name.replace("_p_", "_c_")
            if call_fp.exists():
                try:
                    call_df = pd.read_parquet(call_fp)
                    call_df["ts_event"] = pd.to_datetime(call_df["ts_event"], utc=True).dt.tz_convert("America/New_York")
                    call_df = call_df.dropna(subset=["bid_px_00", "ask_px_00"])
                    call_df = call_df[(call_df["bid_px_00"] > 0) & (call_df["ask_px_00"] > 0)]
                    call_df["ts_min"] = call_df["ts_event"].dt.floor("min")
                    call_df["strike"] = call_df["symbol"].apply(_parse_strike)
                    put_df = df.dropna(subset=["bid_px_00", "ask_px_00"])
                    put_df = put_df[(put_df["bid_px_00"] > 0) & (put_df["ask_px_00"] > 0)]
                    put_df["ts_min"] = put_df["ts_event"].dt.floor("min")
                    put_df["strike"] = put_df["symbol"].apply(_parse_strike)
                    entry_ts_naive = day.tz_localize("America/New_York").replace(hour=ENTRY_H, minute=ENTRY_M)
                    parity_spx = _spx_from_parity(call_df, put_df, entry_ts_naive)
                    if parity_spx is not None:
                        spx_at_1030 = parity_spx
                except Exception:
                    pass
            # Apply filter
            if spx_at_1030 < spot:
                skipped_direction += 1
                continue

        pmps = _day_per_minute_per_strike(df)
        if pmps.empty:
            continue

        entry_ts = day.tz_localize("America/New_York").replace(hour=ENTRY_H, minute=ENTRY_M)
        all_strikes = sorted(pmps.index.get_level_values("strike").unique())

        # Find entry minute snapshot for delta IV (need bid/ask at entry across the chain)
        try:
            entry_quotes = pmps.xs(entry_ts, level="ts_min")
        except KeyError:
            # Try closest minute >= entry_ts
            mins_in_day = sorted(pmps.index.get_level_values("ts_min").unique())
            future = [m for m in mins_in_day if m >= entry_ts]
            if not future:
                skipped_no_quote += 1
                continue
            entry_ts = future[0]
            try:
                entry_quotes = pmps.xs(entry_ts, level="ts_min")
            except KeyError:
                skipped_no_quote += 1
                continue

        processed += 1
        # Time-to-expiry: assume EOD = 16:00 ET → T in years
        secs_to_expiry = (entry_ts.replace(hour=16, minute=0) - entry_ts).total_seconds()
        T = max(secs_to_expiry, 60) / (365.25 * 24 * 3600)

        # ── (BOT-ACTUAL) Credit-seeking strike selection (matches live bot) ──
        # Target short 1% OTM (per config otm_pct=0.010), scan 5 nearest strikes,
        # pick credit closest to $2, reject if credit < $1.20.
        target_short_bot = int(round(spot * (1 - 0.010) / 5) * 5)
        candidates_bot = sorted(all_strikes, key=lambda s: abs(s - target_short_bot))[:5]
        try:
            quotes_bot = pmps.xs(entry_ts, level="ts_min")
        except KeyError:
            quotes_bot = None
        if quotes_bot is not None:
            best_bot = None
            best_diff_bot = float("inf")
            for short_k in candidates_bot:
                long_k = short_k - SPREAD_WIDTH
                if short_k not in quotes_bot.index or long_k not in quotes_bot.index:
                    continue
                s = quotes_bot.loc[short_k]; l = quotes_bot.loc[long_k]
                s_mid = (s["bid_px_00"] + s["ask_px_00"]) / 2
                l_mid = (l["bid_px_00"] + l["ask_px_00"]) / 2
                credit = s_mid - l_mid
                if credit < 1.20: continue
                diff = abs(credit - 2.0)
                if diff < best_diff_bot:
                    best_diff_bot = diff
                    best_bot = (int(short_k), int(long_k))
            if best_bot:
                short_k, long_k = best_bot
                spr = _spread_series(pmps, short_k, long_k)
                if not spr.empty:
                    r = _simulate(spr, entry_ts)
                    if r:
                        r["day"] = str(day.date())
                        r["short_k"] = short_k
                        r["long_k"] = long_k
                        bot_actual_trades.append(r)

        # ── (A) OTM sweep ────────────────────────────────────────────
        for otm_pct in OTM_SWEEP:
            target_short = spot * (1 - otm_pct / 100)
            short_k = int(round(target_short / 5) * 5)
            long_k = short_k - SPREAD_WIDTH
            if short_k not in all_strikes or long_k not in all_strikes:
                continue
            spr = _spread_series(pmps, short_k, long_k)
            if spr.empty:
                continue
            r = _simulate(spr, entry_ts, "open_credit")
            if r:
                r["day"] = str(day.date())
                r["otm_pct"] = otm_pct
                r["short_k"] = short_k
                r["long_k"] = long_k
                otm_trades[otm_pct].append(r)

        # ── (B) Delta-based strike selection ─────────────────────────
        # Per-strike IV from put market price (mid bid/ask) at entry
        # Then pick short with |delta| closest to target
        strike_deltas: list[tuple[int, float, float]] = []  # (strike, delta, iv)
        for strike, quote in entry_quotes.iterrows():
            mid_price = (quote["bid_px_00"] + quote["ask_px_00"]) / 2
            if mid_price <= 0 or strike <= 0:
                continue
            if strike >= spot:
                continue  # ITM puts — outside our universe
            iv = _implied_vol(mid_price, spot, strike, T, r=0.05)
            if iv is None:
                continue
            _, dlt = _bs_put(spot, strike, T, 0.05, iv)
            strike_deltas.append((strike, dlt, iv))

        if strike_deltas:
            for tgt_delta in DELTA_TARGETS:
                # Pick short with |delta| closest to tgt_delta (signed delta is negative for puts)
                best = min(strike_deltas, key=lambda x: abs(abs(x[1]) - tgt_delta))
                short_k = best[0]
                long_k = short_k - SPREAD_WIDTH
                if short_k not in all_strikes or long_k not in all_strikes:
                    continue
                spr = _spread_series(pmps, short_k, long_k)
                if spr.empty:
                    continue
                sim = _simulate(spr, entry_ts, "open_credit")
                if sim:
                    sim["day"] = str(day.date())
                    sim["delta_target"] = tgt_delta
                    sim["short_delta_actual"] = best[1]
                    sim["short_iv"] = best[2]
                    sim["short_k"] = short_k
                    sim["long_k"] = long_k
                    delta_trades[tgt_delta].append(sim)

        if (i + 1) % 100 == 0:
            print(f"  processed {i+1}/{len(files)}  "
                  f"otm@0.75={len(otm_trades[0.75])}  "
                  f"delta@0.10={len(delta_trades[0.10])}")

    print(f"\n[analyze] done: {processed} days processed, "
          f"{skipped_no_quote} no-entry-quote, {skipped_no_credit} below-min-credit, "
          f"{skipped_direction} direction-filter, {skipped_vix} vix-filter")

    # ──────────────────────────────────────────────────────────────────
    # REPORT
    # ──────────────────────────────────────────────────────────────────
    out = {}

    YEARS = 13.1
    # ── (BOT-ACTUAL) Live bot's credit-seeking strategy ──────────
    print(f"\n{'='*78}")
    print(f"(BOT-ACTUAL) Live bot strategy: seed 0.75% OTM, scan 5 strikes, pick credit≈$2, min $1.20")
    print(f"{'='*78}")
    if bot_actual_trades:
        pnls_mid = [t["pnl_pts_assumed_mid"] for t in bot_actual_trades]
        pnls_real = [t["pnl_pts_real_close"] for t in bot_actual_trades]
        credits = [t["entry_credit"] for t in bot_actual_trades]
        wins = sum(1 for p in pnls_mid if p > 0)
        sl = sum(1 for t in bot_actual_trades if t["sl_fired"])
        avg_mid = np.mean(pnls_mid)
        avg_real = np.mean(pnls_real)
        total_mid = sum(pnls_mid) * 100
        total_real = sum(pnls_real) * 100
        avg_credit = np.mean(credits)
        print(f"  trades: {len(bot_actual_trades)}  win%: {wins/len(bot_actual_trades)*100:.1f}  "
              f"sl%: {sl/len(bot_actual_trades)*100:.1f}  avg_credit: ${avg_credit:.2f}")
        print(f"  avg PnL/trade (mid): +${avg_mid*100:.2f}   (real-close): +${avg_real*100:.2f}")
        print(f"  total PnL (mid): ${total_mid:,.0f}   (real-close): ${total_real:,.0f}")
        print(f"  per year @ 1 contract (mid): ${total_mid/YEARS:,.0f}/yr")
        print(f"  per year @ 1 contract (real): ${total_real/YEARS:,.0f}/yr")
        out["BOT_ACTUAL"] = {
            "trades": len(bot_actual_trades),
            "win_rate": wins/len(bot_actual_trades),
            "sl_rate": sl/len(bot_actual_trades),
            "avg_credit": avg_credit,
            "avg_pnl_mid_dollars": avg_mid * 100,
            "avg_pnl_real_dollars": avg_real * 100,
            "total_pnl_mid_dollars": total_mid,
            "total_pnl_real_dollars": total_real,
            "per_year_mid": total_mid / YEARS,
            "per_year_real": total_real / YEARS,
        }

    # ── (A) OTM sweep ─────────────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"(A) OTM SWEEP — fixed 50pt width, 10:30 entry, SL=2.0×, open_credit fill basis")
    print(f"{'='*78}")
    print(f"{'OTM%':>6} {'N':>5} {'Win%':>6} {'SL%':>6} {'AvgPnL':>8} {'TotalP&L':>11} {'Sharpe':>7} {'$/yr':>10}")
    YEARS = 13.1  # 2013-04 → 2026-05
    otm_results = []
    for otm_pct in OTM_SWEEP:
        ts = otm_trades[otm_pct]
        if not ts: continue
        pnls_mid = [t["pnl_pts_assumed_mid"] for t in ts]
        pnls_real = [t["pnl_pts_assumed_mid"] for t in ts]  # mid-based — slippage covered in C
        wins = sum(1 for p in pnls_real if p > 0)
        sl = sum(1 for t in ts if t["sl_fired"])
        avg = np.mean(pnls_real)
        std = np.std(pnls_real)
        total = sum(pnls_real)
        sharpe = avg / std * math.sqrt(252) if std > 0 else 0
        per_yr_dollars = total * 100 / YEARS
        print(f"{otm_pct:>5.2f}% {len(ts):>5} {wins/len(ts)*100:>5.1f}% {sl/len(ts)*100:>5.1f}% "
              f"{avg:>7.3f} {total*100:>10.0f} {sharpe:>6.2f} ${per_yr_dollars:>7.0f}")
        otm_results.append({
            "otm_pct": otm_pct, "n": len(ts),
            "win_rate": wins/len(ts), "sl_rate": sl/len(ts),
            "avg_pnl_pts": avg, "total_pnl_dollars": total*100,
            "sharpe": sharpe, "dollars_per_year": per_yr_dollars,
            "avg_pnl_assumed_mid": float(np.mean(pnls_mid)),
            "total_pnl_assumed_mid_dollars": sum(pnls_mid)*100,
        })
    out["A_otm_sweep"] = otm_results

    # ── (B) Delta-based ───────────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"(B) DELTA-BASED STRIKE SELECTION — short put at target |delta|, 50pt width")
    print(f"{'='*78}")
    print(f"{'TgtΔ':>5} {'N':>5} {'Win%':>6} {'SL%':>6} {'AvgPnL':>8} {'TotalP&L':>11} {'Sharpe':>7} {'$/yr':>10} {'avgIV':>6}")
    delta_results = []
    for d in DELTA_TARGETS:
        ts = delta_trades[d]
        if not ts: continue
        pnls = [t["pnl_pts_assumed_mid"] for t in ts]  # mid-based
        wins = sum(1 for p in pnls if p > 0)
        sl = sum(1 for t in ts if t["sl_fired"])
        avg = np.mean(pnls); std = np.std(pnls)
        sharpe = avg / std * math.sqrt(252) if std > 0 else 0
        per_yr = sum(pnls) * 100 / YEARS
        avg_iv = np.mean([t["short_iv"] for t in ts])
        print(f"{d:>5.2f} {len(ts):>5} {wins/len(ts)*100:>5.1f}% {sl/len(ts)*100:>5.1f}% "
              f"{avg:>7.3f} {sum(pnls)*100:>10.0f} {sharpe:>6.2f} ${per_yr:>7.0f} {avg_iv*100:>5.1f}%")
        delta_results.append({
            "delta_target": d, "n": len(ts),
            "win_rate": wins/len(ts), "sl_rate": sl/len(ts),
            "avg_pnl_pts": avg, "total_pnl_dollars": sum(pnls)*100,
            "sharpe": sharpe, "dollars_per_year": per_yr,
            "avg_short_iv": avg_iv,
        })
    out["B_delta_sweep"] = delta_results

    # ── (C) True slippage ─────────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"(C) TRUE FILL COST vs MID — slippage analysis (baseline OTM=0.75%)")
    print(f"{'='*78}")
    baseline = otm_trades[0.75]
    if baseline:
        # Slippage = (assumed_mid_pnl) - (real_close_pnl); positive = real worse
        slip = [t["pnl_pts_assumed_mid"] - t["pnl_pts_real_close"] for t in baseline]
        sl_only = [t["pnl_pts_assumed_mid"] - t["pnl_pts_real_close"] for t in baseline if t["sl_fired"]]
        avg_slip_all = float(np.mean(slip))
        avg_slip_sl = float(np.mean(sl_only)) if sl_only else 0
        total_mid = sum(t["pnl_pts_assumed_mid"] for t in baseline) * 100
        total_real = sum(t["pnl_pts_real_close"] for t in baseline) * 100
        slip_dollars = total_mid - total_real
        slip_pct_of_pnl = slip_dollars / total_mid * 100 if total_mid else 0
        print(f"  trades: {len(baseline)} (SL fires: {sum(1 for t in baseline if t['sl_fired'])})")
        print(f"  avg slippage per trade (all): {avg_slip_all:+.3f} pts  =  ${avg_slip_all*100:+.2f}")
        print(f"  avg slippage per trade (SL only): {avg_slip_sl:+.3f} pts  =  ${avg_slip_sl*100:+.2f}")
        print(f"  total assumed-mid PnL: ${total_mid:,.0f}")
        print(f"  total real-close PnL: ${total_real:,.0f}")
        print(f"  slippage drag: ${slip_dollars:,.0f}  ({slip_pct_of_pnl:.1f}% of assumed PnL)")
        print(f"  per year drag: ${slip_dollars/YEARS:,.0f}/yr")
        out["C_slippage"] = {
            "trades": len(baseline),
            "avg_slip_all_pts": avg_slip_all,
            "avg_slip_sl_pts": avg_slip_sl,
            "total_assumed_mid_dollars": total_mid,
            "total_real_close_dollars": total_real,
            "slip_drag_dollars": slip_dollars,
            "slip_pct_of_pnl": slip_pct_of_pnl,
            "slip_per_year": slip_dollars / YEARS,
        }

    # ── (D) Position sizing ──────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"(D) POSITION SIZING — rules applied to baseline 0.75% OTM real-close trades")
    print(f"{'='*78}")
    baseline = otm_trades[0.75]
    if baseline:
        # Order chronologically
        baseline_sorted = sorted(baseline, key=lambda t: t["day"])
        pnls = [t["pnl_pts_assumed_mid"] for t in baseline_sorted]  # mid-based
        pnls_dollars = [p * 100 for p in pnls]
        n = len(pnls)

        # 1) Flat 1 contract
        cum_flat = np.cumsum(pnls_dollars)
        flat_total = float(cum_flat[-1])
        flat_dd = _max_drawdown(cum_flat)

        # 2) Kelly fractions
        mu, sigma2 = np.mean(pnls_dollars), np.var(pnls_dollars)
        # Kelly fraction = mu / sigma2 (simplified; for normal returns)
        kelly_f = mu / sigma2 if sigma2 > 0 else 0  # contracts per $ of equity
        # Cap kelly contracts at 10 for sanity
        # Apply with $25k starting equity
        equity_start = 25000
        for frac_name, frac_mult in [("full Kelly", 1.0), ("half Kelly", 0.5), ("quarter Kelly", 0.25)]:
            equity = equity_start
            cum = []
            for p in pnls_dollars:
                contracts = max(1, min(10, int(round(kelly_f * frac_mult * equity / 100))))
                equity += contracts * p
                cum.append(equity - equity_start)
            cum = np.array(cum)
            print_sizing(f"{frac_name} (f={kelly_f*frac_mult:.5f})", cum, n, equity_start)

        # 3) VIX-conditional (need VIX at entry — approximate from spx_df not great; use sl_fired+entry as proxy)
        # Without intraday VIX per trade, approximate via VIX from yfinance daily close
        try:
            import yfinance as yf
            vix_df = yf.Ticker("^VIX").history(start="2020-01-01", end="2026-05-22")
            vix_df.index = vix_df.index.tz_localize(None)
            vix_map = vix_df["Close"].to_dict()
            cum = []
            equity = equity_start
            for t in baseline_sorted:
                day_ts = pd.Timestamp(t["day"])
                v = vix_map.get(day_ts, 18)
                if v < 15: contracts = 3
                elif v < 20: contracts = 2
                elif v < 25: contracts = 1
                else: contracts = 0  # skip
                equity += contracts * t["pnl_pts_assumed_mid"] * 100
                cum.append(equity - equity_start)
            cum = np.array(cum)
            print_sizing("VIX-conditional (3/2/1/skip @ <15/<20/<25/≥25)", cum, n, equity_start)
        except Exception as e:
            print(f"  VIX-conditional skipped: {e}")

        # 4) Anti-martingale (double after win, halve after loss, cap [1,4])
        contracts = 1
        equity = equity_start
        cum = []
        for p in pnls_dollars:
            equity += contracts * p
            cum.append(equity - equity_start)
            if p > 0:
                contracts = min(4, contracts * 2)
            else:
                contracts = max(1, contracts // 2)
        print_sizing("anti-martingale (2× win, /2 loss, cap [1,4])", np.array(cum), n, equity_start)

        # 5) Risk-budget: max-loss per trade = 2% of equity
        # max_loss_per_contract = (50pt - entry_credit) × $100 (worst case = full spread minus credit)
        equity = equity_start
        cum = []
        for t in baseline_sorted:
            entry = t["entry_credit"]
            max_loss_per_contract = (SPREAD_WIDTH - entry) * 100  # dollars
            budget = 0.02 * equity
            contracts = max(1, min(10, int(budget / max_loss_per_contract)))
            equity += contracts * t["pnl_pts_real_close"] * 100
            cum.append(equity - equity_start)
        print_sizing("risk-budget (2% equity at risk per trade)", np.array(cum), n, equity_start)

        out["D_position_sizing"] = {
            "baseline_flat_1ctr": {
                "total_pnl_dollars": flat_total,
                "max_drawdown_dollars": flat_dd,
                "trades": n,
                "dollars_per_year": flat_total / YEARS,
            },
            "kelly_full_fraction": kelly_f,
        }

    RESULTS.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n[analyze] results saved to {RESULTS.name}")


def _max_drawdown(cum: np.ndarray) -> float:
    if len(cum) == 0: return 0
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    return float(dd.min())


def print_sizing(label, cum, n, equity_start):
    total = float(cum[-1])
    dd = _max_drawdown(cum)
    YEARS = 13.1
    print(f"  {label}")
    print(f"      total PnL: ${total:>12,.0f}  ({total/equity_start*100:+.0f}% on ${equity_start:,}, ${total/YEARS:,.0f}/yr)")
    print(f"      max drawdown: ${dd:>10,.0f}")


if __name__ == "__main__":
    run()
