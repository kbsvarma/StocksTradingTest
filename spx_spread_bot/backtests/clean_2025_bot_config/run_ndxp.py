"""Clean NDXP 0DTE BPS backtest — same methodology as SPXW clean run.

NDXP config (modeled after live SPXW bot, scaled for NDX):
  - 1 contract × 100pt wide × 1% OTM seed
  - Credit-seek to target $5.00 (NDX scale), min $3.00
  - 10:30 ET entry
  - Direction filter via NDX put-call parity at 10:30 vs NDX open
  - VIX gate (skip if VIX > 25, prior-day close)
  - SL multipliers: 2.0 and 3.0

NDX point value = $100 (same as SPX). So credit/loss numbers in USD = points × $100.
NDX trades at ~$21,000 vs SPX ~$5,300 (4× scale). 100pt NDX width ≈ 25pt SPX width in % terms.

Run as: python -m spx_spread_bot.backtests.clean_2025_bot_config.run_ndxp YEAR [--no-dir-filter]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

DATA_PUTS = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "ndxp_p_cbbo_1m"
DATA_CALLS = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "ndxp_c_cbbo_1m"
OUT_DIR = Path(__file__).parent
OUT_DIR.mkdir(exist_ok=True)

_YEAR = sys.argv[1] if len(sys.argv) > 1 else "2025"
_NO_DIR = "--no-dir-filter" in sys.argv
_suf = "_nodir" if _NO_DIR else ""
RESULT_PATH = OUT_DIR / f"result_ndxp_{_YEAR}{_suf}.json"

# Strategy params (modeled after SPXW config, scaled to NDX)
SPREAD_WIDTH = 100        # NDX uses $25 strike spacing; 100pt = 4 strikes
OTM_PCT_SEED = 0.010      # 1% OTM
TARGET_CREDIT = 5.00      # NDX scale (~4x SPXW's $2 target)
MIN_CREDIT = 3.00         # NDX scale (~2.5x SPXW's $1.20)
STOP_MULTIPLIERS = [2.0, 3.0]
VIX_MAX = 25.0
ENTRY_H, ENTRY_M = 10, 30

_RX = re.compile(r"[CP]0?(\d+)000$")


def _strike(sym: str) -> int:
    m = _RX.search(sym)
    return int(m.group(1)) if m else 0


def _load(fp: Path) -> pd.DataFrame:
    df = pd.read_parquet(fp)
    if df.empty: return pd.DataFrame()
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
    if df.empty: return pd.DataFrame()
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_strike)
    g = df.sort_values("ts_event").groupby(["ts_min", "strike"]).last()
    return g[["bid_px_00", "ask_px_00"]]


def _ndx_from_parity(c: pd.DataFrame, p: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    try:
        cm = c.xs(ts, level="ts_min")
        pm = p.xs(ts, level="ts_min")
    except KeyError:
        return None
    if cm.empty or pm.empty: return None
    cmid = (cm["bid_px_00"] + cm["ask_px_00"]) / 2
    pmid = (pm["bid_px_00"] + pm["ask_px_00"]) / 2
    m = pd.DataFrame({"c": cmid, "p": pmid}).dropna()
    if m.empty: return None
    m["diff"] = (m["c"] - m["p"]).abs()
    best = m.sort_values("diff").iloc[0]
    return float(best.name) + float(best["c"]) - float(best["p"])


def _credit_seek(quotes: pd.DataFrame, spot: float) -> tuple[int, int, float] | None:
    # NDX strikes at $25 spacing for NDXP usually
    target = int(round(spot * (1 - OTM_PCT_SEED) / 25) * 25)
    strikes = sorted(quotes.index.tolist())
    if not strikes: return None
    cands = sorted(strikes, key=lambda s: abs(s - target))[:5]
    best = None
    best_diff = float("inf")
    for sk in cands:
        lk = sk - SPREAD_WIDTH
        if sk not in quotes.index or lk not in quotes.index: continue
        s = quotes.loc[sk]; l = quotes.loc[lk]
        smid = (s["bid_px_00"] + s["ask_px_00"]) / 2
        lmid = (l["bid_px_00"] + l["ask_px_00"]) / 2
        cmid = smid - lmid
        if cmid < MIN_CREDIT: continue
        d = abs(cmid - TARGET_CREDIT)
        if d < best_diff:
            best_diff = d
            best = (int(sk), int(lk), float(cmid))
    return best


def _spread_series(quotes: pd.DataFrame, sk: int, lk: int) -> pd.DataFrame:
    try:
        s = quotes.xs(sk, level="strike")
        l = quotes.xs(lk, level="strike")
    except KeyError:
        return pd.DataFrame()
    m = s.join(l, lsuffix="_s", rsuffix="_l", how="inner")
    if m.empty: return m
    m["mid"] = (m["bid_px_00_s"] + m["ask_px_00_s"]) / 2 - (m["bid_px_00_l"] + m["ask_px_00_l"]) / 2
    m["close_cost"] = m["ask_px_00_s"] - m["bid_px_00_l"]
    return m[["mid", "close_cost"]]


def _simulate(spread: pd.DataFrame, entry_ts: pd.Timestamp, credit: float, mult: float) -> dict | None:
    stop = credit * mult
    walk = spread[spread.index >= entry_ts]
    if walk.empty: return None
    for ts, r in walk.iterrows():
        if float(r["mid"]) >= stop:
            return {"sl_fired": True, "exit_mid": float(r["mid"]), "exit_real": float(r["close_cost"]),
                    "pnl_mid": credit - float(r["mid"]), "pnl_real": credit - float(r["close_cost"]),
                    "entry_credit": credit, "stop_price": stop}
    final = walk.iloc[-1]
    return {"sl_fired": False, "exit_mid": float(final["mid"]), "exit_real": float(final["close_cost"]),
            "pnl_mid": credit - float(final["mid"]), "pnl_real": credit - float(final["close_cost"]),
            "entry_credit": credit, "stop_price": stop}


def main():
    print(f"[ndxp-clean-{_YEAR}] loading NDX/VIX daily history...")
    ndx = yf.Ticker("^NDX").history(start=f"{int(_YEAR)-1}-12-15", end=f"{int(_YEAR)+1}-01-05")
    ndx.index = ndx.index.tz_localize(None) if ndx.index.tz else ndx.index
    vix = yf.Ticker("^VIX").history(start=f"{int(_YEAR)-1}-12-15", end=f"{int(_YEAR)+1}-01-05")
    vix.index = vix.index.tz_localize(None) if vix.index.tz else vix.index

    files = sorted(DATA_PUTS.glob(f"ndxp_p_cbbo_{_YEAR}*.parquet"))
    print(f"[ndxp-clean-{_YEAR}] {len(files)} put files")

    trades_by = {m: [] for m in STOP_MULTIPLIERS}
    skip = {"vix_gate": 0, "direction_filter": 0, "no_call": 0, "no_parity": 0,
            "no_qualifying_spread": 0, "no_entry_quote": 0}
    days_attempted = 0

    for i, p_fp in enumerate(files):
        date_str = p_fp.stem.split("_")[-1]
        day = pd.Timestamp(f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
        try:
            ndx_open = float(ndx.loc[day, "Open"])
        except (KeyError, IndexError):
            continue
        try:
            v = vix.loc[:day].iloc[-2]["Close"] if day in vix.index else vix.loc[:day].iloc[-1]["Close"]
        except (IndexError, KeyError):
            v = 20.0
        if v >= VIX_MAX:
            skip["vix_gate"] += 1; continue

        c_fp = DATA_CALLS / p_fp.name.replace("_p_", "_c_")
        if not c_fp.exists():
            skip["no_call"] += 1; continue

        pq = _load(p_fp)
        if pq.empty: continue
        cq = _load(c_fp)

        entry_ts = day.tz_localize("America/New_York").replace(hour=ENTRY_H, minute=ENTRY_M)
        ndx_1030 = _ndx_from_parity(cq, pq, entry_ts)
        if ndx_1030 is None:
            skip["no_parity"] += 1; continue
        if not _NO_DIR and ndx_1030 < ndx_open:
            skip["direction_filter"] += 1; continue

        days_attempted += 1
        try:
            eq = pq.xs(entry_ts, level="ts_min")
        except KeyError:
            skip["no_entry_quote"] += 1; continue
        pick = _credit_seek(eq, ndx_1030)
        if pick is None:
            skip["no_qualifying_spread"] += 1; continue
        sk, lk, cmid = pick

        spread = _spread_series(pq, sk, lk)
        if spread.empty: continue

        for mult in STOP_MULTIPLIERS:
            sim = _simulate(spread, entry_ts, cmid, mult)
            if sim is None: continue
            sim["day"] = str(day.date())
            sim["short"] = sk; sim["long"] = lk
            sim["ndx_open"] = ndx_open; sim["ndx_1030"] = ndx_1030; sim["vix"] = float(v)
            trades_by[mult].append(sim)

        if (i+1) % 50 == 0:
            print(f"  processed {i+1}/{len(files)}, trades@2.0={len(trades_by[2.0])}")

    print(f"\n[ndxp-clean-{_YEAR}] DONE")
    print(f"  files: {len(files)}, attempted: {days_attempted}, skipped: {skip}")

    out = {"year": _YEAR, "no_dir": _NO_DIR, "files": len(files), "days_attempted": days_attempted, "skipped": skip, "by_stop": {}}
    for mult in STOP_MULTIPLIERS:
        ts = trades_by[mult]
        if not ts:
            print(f"  STOP {mult}× — no trades")
            continue
        n = len(ts)
        wins_r = sum(1 for t in ts if t["pnl_real"] > 0)
        sl = sum(1 for t in ts if t["sl_fired"])
        pnl_mid = sum(t["pnl_mid"] * 100 for t in ts)
        pnl_real = sum(t["pnl_real"] * 100 for t in ts)
        avg_credit = np.mean([t["entry_credit"] for t in ts])
        worst = min(t["pnl_real"] * 100 for t in ts)
        pnls = [t["pnl_real"] * 100 for t in ts]
        sharpe = np.mean(pnls) / (np.std(pnls) + 1e-9) * np.sqrt(250) if len(pnls) > 1 else 0
        cum = np.cumsum(pnls); peak = np.maximum.accumulate(cum)
        max_dd = float((cum - peak).min())

        out["by_stop"][str(mult)] = {
            "trades": n, "wins_real": wins_r, "win_pct_real": round(wins_r/n*100, 1),
            "sl_fires": sl, "sl_pct": round(sl/n*100, 1),
            "avg_credit": round(avg_credit, 2),
            "total_pnl_mid_USD": round(pnl_mid, 0),
            "total_pnl_real_USD": round(pnl_real, 0),
            "avg_pnl_real_USD": round(pnl_real/n, 0),
            "worst_day_USD": round(worst, 0),
            "max_drawdown_USD": round(max_dd, 0),
            "sharpe": round(sharpe, 2),
        }
        print(f"  STOP {mult}×:  n={n} win%={wins_r/n*100:.1f} sl%={sl/n*100:.1f} cred=${avg_credit:.2f}  "
              f"pnl_mid=${pnl_mid:+,.0f}  pnl_real=${pnl_real:+,.0f}  worst=${worst:,.0f} mdd=${max_dd:,.0f} shrp={sharpe:.2f}")

    RESULT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"[ndxp-clean-{_YEAR}] saved {RESULT_PATH.name}")


if __name__ == "__main__":
    main()
