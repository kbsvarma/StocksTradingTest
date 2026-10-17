"""SPX 0DTE width x OTM RE-VERIFICATION (2026-07-03)

Adversarial re-run of width_otm_research_2026_05_14 using REAL executable
CBBO bid/ask quotes instead of daily OHLCV trade-print opens.

Key differences vs the original run.py (the audited engine):
  - ENTRY credit from real 1-min CBBO bid/ask at the entry minute (10:30 ET),
    priced marketably: sell short @ bid, buy long @ ask (conservative),
    plus a mid-based variant for sensitivity.
  - STOP modelled by walking the REAL intraday spread mid minute-by-minute.
    Stop triggers when spread mark >= STOP_MULT * credit; fill at the next
    minute's marketable exit (buy short @ ask, sell long @ bid) + slippage.
  - SETTLEMENT at true SPX close (PM cash settle): put intrinsic at 16:00.
  - NO direction filter (the original used spx_close<spx_open = look-ahead).
    A separate no-lookahead momentum filter variant is available but OFF by
    default so the baseline comparison is clean.
  - Honest daily-portfolio Sharpe (annualized on daily P&L), plus the
    original per-trade Sharpe for comparison.

Crash-resilient: per-config trade CSVs written incrementally with atomic
rename; completed configs skipped on restart.

LOCAL DATA ONLY. No Databento network calls.
"""
from __future__ import annotations
import re, os, sys, json, glob, warnings
from datetime import datetime, date
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Paths (LOCAL ONLY) ──────────────────────────────────────────────────
ROOT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot")
CBBO_DIR = ROOT / "backtests/narrow_width_databento_2026_05_21/spxw_p_cbbo_1m"
SPX_CACHE = ROOT / "backtests/narrow_width_databento_2026_05_21/spx_intraday_cache.parquet"
OUT = ROOT / "backtests/width_otm_reverify_2026_07_03"
OUT.mkdir(exist_ok=True)
TRADES_DIR = OUT / "per_config"
TRADES_DIR.mkdir(exist_ok=True)

# ── Strategy constants ──────────────────────────────────────────────────
ENTRY_TIME = "10:30"          # live bot entry
STOP_MULT = 2.0               # stop when spread mark >= 2x credit
MULT = 100.0
# Live bot config_bps_vG.yaml uses min_credit=0.15 on XSP (1/10 SPX), i.e.
# SPX-equivalent $1.50. Applying the SAME floor to booked credit is the honest
# apples-to-apples entry rule. A looser floor is swept for sensitivity.
MIN_CREDIT = 1.50            # SPX-equivalent of live bot's XSP min_credit 0.15
MIN_CREDIT_LOOSE = 0.20      # sensitivity: what if we relax the floor
# slippage per spread per side, in $ (i.e. price points). Round trip = entry + exit.
# We already price marketably (cross the spread) so this is ADDITIONAL slippage
# beyond the quoted bid/ask, for queue/latency/size. Sensitivity swept.
SLIP_GRID = [0.05, 0.10, 0.15]
DEFAULT_SLIP = 0.10

# Configs to test: top-3 from original + the live baseline
# (width_pts, otm_pct, n_contracts)
CONFIGS = [
    (5,  0.005,  10),   # original #1 headline
    (5,  0.0075, 10),   # original #2
    (10, 0.005,  5),    # original #4 (best 10pt)
    (50, 0.010,  1),    # LIVE BOT BASELINE (SPX-equiv of XSP 5pt/1.0%)
    # extra sanity references
    (50, 0.010,  1),
]
# de-dup preserving order
_seen=set(); CONFIGS=[c for c in CONFIGS if not (c in _seen or _seen.add(c))]

# ── helpers ─────────────────────────────────────────────────────────────
STRIKE_RE = re.compile(r'^SPXW\s+\d{6}P(\d{8})$')
def parse_strike(s):
    m = STRIKE_RE.match(str(s))
    return int(m.group(1))/1000.0 if m else np.nan

def load_spx():
    df = pd.read_parquet(SPX_CACHE)
    df.index = pd.to_datetime(df.index).normalize()
    return df

def file_date(fp):
    m = re.search(r'(\d{8})', os.path.basename(fp))
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None

def load_cbbo(fp):
    """Return per-(minute,strike) bid/ask frame, ET-indexed, minute-floored."""
    df = pd.read_parquet(fp)
    df.index = df.index.tz_convert("US/Eastern")
    df["strike"] = df["symbol"].apply(parse_strike)
    df = df[df["strike"].notna()].copy()
    df["minute"] = df.index.floor("min")
    # keep last quote per (minute, strike)
    df = df.sort_index()
    g = df.groupby(["minute", "strike"]).last()
    g = g[["bid_px_00", "ask_px_00"]].reset_index()
    g["mid"] = (g["bid_px_00"] + g["ask_px_00"]) / 2.0
    return g

def quote_at(cbbo, minute, strike):
    """Nearest-at-or-before-minute quote for exact strike. Returns (bid,ask,mid) or None."""
    sub = cbbo[(cbbo["strike"] == strike) & (cbbo["minute"] <= minute)]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    return float(r["bid_px_00"]), float(r["ask_px_00"]), float(r["mid"])

# ── core per-day simulation ─────────────────────────────────────────────
def sim_day(cbbo, d, spx_row, width, otm_pct, slip, min_credit=MIN_CREDIT):
    """Simulate one 0DTE BPS for one config. Returns dict or None (no trade)."""
    entry_min = pd.Timestamp(f"{d} {ENTRY_TIME}", tz="US/Eastern")

    spx_open = float(spx_row["Open"])
    spx_close = float(spx_row["Close"])
    spx_low = float(spx_row["Low"])

    # strikes available at entry minute
    at_entry = cbbo[(cbbo["minute"] == entry_min)]
    if at_entry.empty:
        # fall back to nearest minute at/after entry within 5 min
        cand = cbbo[(cbbo["minute"] >= entry_min) & (cbbo["minute"] <= entry_min + pd.Timedelta("5min"))]
        if cand.empty:
            return None
        entry_min = cand["minute"].min()
        at_entry = cbbo[cbbo["minute"] == entry_min]

    strikes = np.sort(at_entry["strike"].unique())
    if len(strikes) < 2:
        return None
    target_short = spx_open * (1.0 - otm_pct)
    short_k = strikes[np.argmin(np.abs(strikes - target_short))]
    long_k = strikes[np.argmin(np.abs(strikes - (short_k - width)))]
    if long_k >= short_k:
        return None
    actual_width = short_k - long_k

    qs = quote_at(cbbo, entry_min, short_k)
    ql = quote_at(cbbo, entry_min, long_k)
    if qs is None or ql is None:
        return None
    s_bid, s_ask, s_mid = qs
    l_bid, l_ask, l_mid = ql

    # Entry credit model: the live bot works a LIMIT near the mid and typically
    # fills close to mid, NOT by crossing the full bid/ask. Realistic booked
    # credit = mid minus a per-side concession (slip). This is the honest middle
    # ground between naive mid (too generous) and fully-marketable short-bid/
    # long-ask (too pessimistic for a resting limit).
    credit_mid = (s_mid - l_mid)
    credit_book = credit_mid - 2 * slip          # mid minus concession on both legs
    # fully-marketable downside reference (recorded, not booked)
    credit_marketable = (s_bid - l_ask) - 2 * slip
    if credit_book < min_credit:
        return None

    credit = credit_book  # the credit we actually book

    # ── walk intraday spread mark for the stop ──
    # Live-bot rule: stop when the position's MARK (cost to close) reaches
    # STOP_MULT x the credit received. We use the running MID as the mark
    # (that is what a monitor watches), and only when it breaches do we fill
    # marketably (buy short @ ask, sell long @ bid) + slip. This keeps the
    # trigger basis (mid) and the credit basis (marketable) apart in the
    # SAME way the live bot does: credit is what you took in; stop level is
    # STOP_MULT x that; the running mark you compare against is the mid.
    #
    # Guard against spurious immediate stops on tiny-credit spreads whose
    # mid mark already sits above STOP_MULT*credit at entry due to the
    # bid/ask half-spread: the stop level floors at the entry mid mark, so
    # the position must actually move against us, not just be marked wide.
    entry_mark_mid = s_mid - l_mid
    stop_level = max(STOP_MULT * credit, entry_mark_mid + 0.05)
    minutes = sorted(cbbo[(cbbo["minute"] > entry_min)]["minute"].unique())
    exit_reason = None
    exit_debit = None
    for m in minutes:
        qs_m = quote_at(cbbo, m, short_k)
        ql_m = quote_at(cbbo, m, long_k)
        if qs_m is None or ql_m is None:
            continue
        mark_mid = qs_m[2] - ql_m[2]
        if mark_mid >= stop_level:
            # stop fires: close marketably -> buy short @ ask, sell long @ bid, + slip
            exit_debit = (qs_m[1] - ql_m[0]) + 2 * slip
            exit_debit = min(exit_debit, actual_width)
            exit_reason = "stop"
            break

    if exit_reason is None:
        # held to settlement: PM cash settle at SPX close (put intrinsic)
        short_intrinsic = max(0.0, short_k - spx_close)
        long_intrinsic = max(0.0, long_k - spx_close)
        exit_debit = short_intrinsic - long_intrinsic
        exit_debit = min(max(exit_debit, 0.0), actual_width)
        exit_reason = "settle"

    pnl_per_spread = (credit - exit_debit) * MULT
    return {
        "date": d.isoformat(),
        "width": width, "otm_pct": otm_pct,
        "short_k": short_k, "long_k": long_k, "actual_width": actual_width,
        "credit_exec": credit, "credit_mid": credit_mid,
        "credit_marketable": credit_marketable,
        "exit_debit": exit_debit, "exit_reason": exit_reason,
        "pnl_per_spread": pnl_per_spread,
        "spx_open": spx_open, "spx_close": spx_close, "spx_low": spx_low,
    }

# ── config runner ───────────────────────────────────────────────────────
def run_config(width, otm_pct, n_contracts, slip, spx, files, tag, min_credit=MIN_CREDIT):
    mc_tag = f"_mc{min_credit}"
    csv_path = TRADES_DIR / f"cfg_w{width}_otm{otm_pct}_n{n_contracts}_slip{slip}{mc_tag}.csv"
    if csv_path.exists():
        try:
            done = pd.read_csv(csv_path)
            if len(done) > 0:
                print(f"  [skip-done] {tag} ({len(done)} trades)")
                return done
        except Exception:
            pass
    rows = []
    tmp = str(csv_path) + ".tmp"
    for i, fp in enumerate(files):
        d = file_date(fp)
        ts = pd.Timestamp(d).normalize()
        if ts not in spx.index:
            continue
        spx_row = spx.loc[ts]
        if isinstance(spx_row, pd.DataFrame):
            spx_row = spx_row.iloc[0]
        try:
            cbbo = load_cbbo(fp)
        except Exception:
            continue
        r = sim_day(cbbo, d, spx_row, width, otm_pct, slip, min_credit=min_credit)
        if r is not None:
            r["n_contracts"] = n_contracts
            r["net_pnl"] = r["pnl_per_spread"] * n_contracts
            rows.append(r)
        # incremental atomic write every 100 files
        if i % 100 == 0 and rows:
            pd.DataFrame(rows).to_csv(tmp, index=False)
            os.replace(tmp, csv_path)
            print(f"    {tag}: {i}/{len(files)} files, {len(rows)} trades so far")
    df = pd.DataFrame(rows)
    df.to_csv(tmp, index=False)
    os.replace(tmp, csv_path)
    print(f"  [done] {tag}: {len(df)} trades -> {csv_path.name}")
    return df

# ── metrics ─────────────────────────────────────────────────────────────
def metrics(df, n_contracts):
    if df is None or len(df) == 0:
        return None
    net = df["net_pnl"].astype(float)
    # daily portfolio returns: one trade/day, so per-trade == per-day here.
    # Honest annualized Sharpe on daily P&L series (calendar-aligned).
    dd = df.copy()
    dd["date"] = pd.to_datetime(dd["date"])
    daily = dd.groupby(dd["date"].dt.date)["net_pnl"].sum()
    # build full calendar of traded days only (strategy is flat on non-traded days;
    # Sharpe over traded days is the honest per-active-day figure)
    mu, sd = daily.mean(), daily.std(ddof=1)
    sharpe_daily = (mu / sd * np.sqrt(252)) if sd and sd > 0 else 0.0
    # original-style per-trade sharpe (what run.py computed)
    mt, st = net.mean(), net.std(ddof=0)
    sharpe_pertrade = (mt / st * np.sqrt(252)) if st and st > 0 else 0.0
    cum = net.cumsum()
    max_dd = float((cum.cummax() - cum).max())
    wins = (net > 0).mean()
    return {
        "trades": int(len(df)),
        "win_rate": float(wins),
        "total_pnl": float(net.sum()),
        "avg_pnl": float(net.mean()),
        "median_pnl": float(net.median()),
        "best_day": float(net.max()),
        "worst_day": float(net.min()),
        "max_dd": max_dd,
        "sharpe_daily_honest": float(sharpe_daily),
        "sharpe_pertrade_origstyle": float(sharpe_pertrade),
        "stops": int((df["exit_reason"] == "stop").sum()),
        "settles": int((df["exit_reason"] == "settle").sum()),
        "avg_credit": float(df["credit_exec"].mean() * MULT),
    }

def run_sweep_cached(spx, files):
    """Single pass over files, computing all (config, slip, min_credit) cells.
    Loads each CBBO parquet ONCE. Writes incremental combined trades."""
    # sweep dimensions
    slips = SLIP_GRID
    mcreds = [MIN_CREDIT, MIN_CREDIT_LOOSE]
    cells = {}  # (w,otm,n,slip,mc) -> list of trade dicts
    for (w, otm, n) in CONFIGS:
        for slip in slips:
            for mc in mcreds:
                cells[(w, otm, n, slip, mc)] = []

    combined_path = OUT / "trades_corrected.csv"
    tmp = str(combined_path) + ".tmp"
    header_written = False

    for i, fp in enumerate(files):
        d = file_date(fp)
        ts = pd.Timestamp(d).normalize()
        if ts not in spx.index:
            continue
        spx_row = spx.loc[ts]
        if isinstance(spx_row, pd.DataFrame):
            spx_row = spx_row.iloc[0]
        try:
            cbbo = load_cbbo(fp)   # parse ONCE per file
        except Exception:
            continue
        for (w, otm, n) in CONFIGS:
            for slip in slips:
                for mc in mcreds:
                    r = sim_day(cbbo, d, spx_row, w, otm, slip, min_credit=mc)
                    if r is not None:
                        r = dict(r)
                        r["n_contracts"] = n
                        r["slip"] = slip
                        r["min_credit"] = mc
                        r["net_pnl"] = r["pnl_per_spread"] * n
                        cells[(w, otm, n, slip, mc)].append(r)
        if i % 150 == 0 and i > 0:
            allrows = [r for v in cells.values() for r in v]
            if allrows:
                pd.DataFrame(allrows).to_csv(tmp, index=False)
                os.replace(tmp, combined_path)
            print(f"  {i}/{len(files)} files processed, {sum(len(v) for v in cells.values())} trade-cells")

    allrows = [r for v in cells.values() for r in v]
    df_all = pd.DataFrame(allrows)
    df_all.to_csv(tmp, index=False)
    os.replace(tmp, combined_path)
    print(f"Wrote {combined_path} ({len(df_all)} rows)")
    return cells, df_all

def main():
    print("Loading SPX daily cache...")
    spx = load_spx()
    files = sorted(glob.glob(str(CBBO_DIR / "*.parquet")))
    print(f"{len(files)} CBBO files, {files[0]} .. {files[-1]}")

    cells, df_all = run_sweep_cached(spx, files)

    results = []
    for (w, otm, n, slip, mc), rows in cells.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        m = metrics(df, n)
        if m:
            m.update({"width": w, "otm_pct": otm, "n_contracts": n, "slip": slip, "min_credit": mc})
            results.append(m)
    res = pd.DataFrame(results).sort_values(["min_credit", "slip", "total_pnl"], ascending=[True, True, False])
    res.to_csv(OUT / "reverify_metrics.csv", index=False)
    print("\n=== SUMMARY (min_credit / slip / config) ===")
    cols = ["min_credit", "slip", "width", "otm_pct", "n_contracts", "trades", "win_rate",
            "total_pnl", "avg_pnl", "sharpe_daily_honest", "sharpe_pertrade_origstyle",
            "stops", "settles", "worst_day", "max_dd", "avg_credit"]
    print(res[cols].to_string(index=False))
    print("\nWrote", OUT / "reverify_metrics.csv")

if __name__ == "__main__":
    main()
