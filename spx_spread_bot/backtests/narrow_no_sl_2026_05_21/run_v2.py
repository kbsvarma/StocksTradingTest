"""Narrow no-SL backtest v2 — fixes both methodology bugs.

Bug 1 fix: Limit widths to 5, 10, 15, 20pt (the only strikes 12-year SPXW
chain actually has). Note explicitly that 1-3pt cannot be tested.

Bug 2 fix: Use ES futures 1-min data (2020-2024) as proxy for SPX at 10:30 ET.
Direction filter = (ES_at_10:30 > ES_at_open_9:30). This is point-in-time —
captures days that LOOKED bullish at entry but reversed by close (the real
production failure case missed in v1).

For the 2014-2019 period without minute data, we run a SEPARATE pass using
the permissive proxy (spx_high >= spx_open) and label it as such.

Grid (no 1-3pt since unsupported):
  widths      = [5, 10, 15, 20]
  otm_pcts    = [0.0025, 0.005, 0.0075, 0.010]
  profit_tgts = [None, 0.50, 0.25]
  sl_mults    = [None, 2.0, 3.0]

Output: results/summary_v2.csv with metadata flag for which period/filter.
"""
from __future__ import annotations

import warnings
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)

CACHE_DIR = Path(__file__).parent.parent / "narrow_width_sweep_2026_05_21" / "results"
SPX_CACHE = CACHE_DIR / "spx_vix_daily.parquet"
OPTS_CACHE = CACHE_DIR / "spxw_0dte_puts.parquet"
ES_1MIN_PATH = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/data/databento/es/es_1min_5yr.dbn")
ES_AT_1030_CACHE = OUT / "es_10_30_et.parquet"

SL_FILL_MULT = 2.5
SLIP_PER_SPREAD_PER_SIDE = 0.05
MIN_CREDIT = 0.05
MULT = 100.0
VIX_MIN, VIX_MAX = 12.0, 25.0
COMMISSION_PER_TRADE = 2.60

WIDTHS = [5, 10, 15, 20]
OTM_PCTS = [0.0025, 0.005, 0.0075, 0.010]
PROFIT_TARGETS = [None, 0.50, 0.25]
SL_MULTS = [None, 2.0, 3.0]


def _log(msg: str) -> None:
    print(f"[narrow-no-sl-v2] {msg}", flush=True)


def build_es_at_1030() -> pd.DataFrame:
    """Build {trading_day → (es_open, es_1030, es_close)} from ES 1-min data.
    Point-in-time: ES at 10:30 ET tells us whether direction filter would
    have passed in production.
    """
    if ES_AT_1030_CACHE.exists():
        _log(f"using cached ES 10:30 data: {ES_AT_1030_CACHE}")
        return pd.read_parquet(ES_AT_1030_CACHE)

    _log(f"loading ES 1-min data ({ES_1MIN_PATH.stat().st_size // (1024*1024)} MB)...")
    import databento as db
    df = db.DBNStore.from_file(ES_1MIN_PATH).to_df()
    # Index is UTC timestamps. Convert to ET.
    df = df.tz_convert("America/New_York") if df.index.tz else df.tz_localize("UTC").tz_convert("America/New_York")
    # Filter to RTH (regular trading hours)
    df["date"] = df.index.date
    df["time"] = df.index.time
    # We want ES open at 9:30 ET and ES at 10:30 ET for each trading day
    from datetime import time as dtime
    open_t = dtime(9, 30)
    entry_t = dtime(10, 30)
    out = []
    for d, g in df.groupby("date"):
        g_open = g[g["time"] == open_t]
        g_entry = g[g["time"] == entry_t]
        if g_open.empty or g_entry.empty:
            continue
        out.append({
            "trading_day": d,
            "es_open_9_30": float(g_open["close"].iloc[0]),
            "es_at_10_30": float(g_entry["close"].iloc[0]),
            "es_close": float(g.iloc[-1]["close"]),
            "es_low": float(g["low"].min()),
        })
    es_df = pd.DataFrame(out)
    es_df.to_parquet(ES_AT_1030_CACHE)
    _log(f"cached ES 10:30 for {len(es_df)} trading days")
    return es_df


def load_data():
    if not SPX_CACHE.exists() or not OPTS_CACHE.exists():
        _log("ERROR: cached SPX/options missing — run narrow_width_sweep_2026_05_21 first")
        sys.exit(1)
    spx = pd.read_parquet(SPX_CACHE)
    opts_df = pd.read_parquet(OPTS_CACHE)
    opts_by_day = {d: g[["strike","open","close"]].reset_index(drop=True)
                   for d, g in opts_df.groupby("trading_day")}
    es_df = build_es_at_1030()
    es_df["trading_day"] = pd.to_datetime(es_df["trading_day"])
    es_df = es_df.set_index("trading_day")
    return spx, opts_by_day, es_df


@dataclass
class Trade:
    date: date
    width: float
    otm_pct: float
    profit_target: object
    sl_mult: object
    short_strike: float
    long_strike: float
    actual_width: float
    credit: float
    spx_open: float
    spx_low: float
    spx_close: float
    vix: float
    pnl_pts: float
    pnl_usd: float
    exit_reason: str


def simulate(opts_by_day: dict, spx_vix: pd.DataFrame, es_df: pd.DataFrame,
             width: int, otm_pct: float, profit_target, sl_mult,
             filter_mode: str = "point_in_time") -> list[Trade]:
    """
    filter_mode:
      'point_in_time' — use ES at 10:30 ET vs ES at 9:30 ET (PROPER, recent only)
      'permissive'    — spx_high >= spx_open (older period, no minute data)
    """
    trades = []
    for d, opts in opts_by_day.items():
        ts = pd.Timestamp(d)
        if ts not in spx_vix.index:
            continue
        row = spx_vix.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        spx_open = float(row["spx_open"])
        spx_high = float(row["spx_high"])
        spx_low  = float(row["spx_low"])
        spx_close = float(row["spx_close"])
        vix = float(row["vix_close"]) if not pd.isna(row["vix_close"]) else 20.0

        # Direction filter
        if filter_mode == "point_in_time":
            if ts not in es_df.index:
                continue  # no ES data for this day (pre-2020)
            es = es_df.loc[ts]
            if isinstance(es, pd.DataFrame):
                es = es.iloc[0]
            es_open = float(es["es_open_9_30"])
            es_1030 = float(es["es_at_10_30"])
            if es_1030 < es_open:
                continue  # bot would have skipped (bearish at entry)
        elif filter_mode == "permissive":
            if spx_high < spx_open:
                continue  # never went above open all day → bot couldn't have entered
        # else 'none' → no filter

        # VIX gate
        if not (VIX_MIN <= vix <= VIX_MAX):
            continue

        # Strike selection
        opts_d = opts.copy()
        target_short = spx_open * (1.0 - otm_pct)
        opts_d["d"] = (opts_d["strike"] - target_short).abs()
        if opts_d.empty:
            continue
        sr = opts_d.loc[opts_d["d"].idxmin()]
        short_strike = float(sr["strike"])
        long_target = short_strike - width
        exact = opts_d[opts_d["strike"] == long_target]
        if not exact.empty:
            lr = exact.iloc[0]
        else:
            below = opts_d[opts_d["strike"] < short_strike].copy()
            if below.empty:
                continue
            below["d"] = (below["strike"] - long_target).abs()
            lr = below.loc[below["d"].idxmin()]
        long_strike = float(lr["strike"])
        actual_width = short_strike - long_strike
        if actual_width <= 0:
            continue

        # SAFETY: skip if actual_width != intended width (we want true width experiments)
        # For widths 5/10/15/20 with 5pt strike granularity, actual_width should match exactly
        if actual_width != width:
            continue

        credit = float(sr["open"]) - float(lr["open"])
        if credit < MIN_CREDIT or credit <= 0:
            continue
        credit -= 2 * SLIP_PER_SPREAD_PER_SIDE
        if credit < MIN_CREDIT:
            continue

        mark_close = max(0.0, float(sr["close"]) - float(lr["close"]))
        mark_close = min(mark_close, actual_width)
        if mark_close < 0: mark_close = 0

        sl_triggered = False
        if sl_mult is not None:
            intrinsic_at_low = max(0.0, short_strike - spx_low) - max(0.0, long_strike - spx_low)
            intrinsic_at_low = min(intrinsic_at_low, actual_width)
            if intrinsic_at_low >= credit * sl_mult:
                sl_triggered = True

        pt_triggered = False
        if profit_target is not None and not sl_triggered:
            if mark_close < credit * profit_target:
                pt_triggered = True

        if sl_triggered:
            exit_mark = min(credit * SL_FILL_MULT, actual_width)
            pnl_pts = credit - exit_mark
            reason = "STOPPED"
        elif pt_triggered:
            exit_mark = credit * profit_target
            pnl_pts = credit - exit_mark
            reason = "PROFIT_TARGET"
        else:
            if mark_close <= 0.05:
                pnl_pts = credit; reason = "EXPIRED"
            else:
                pnl_pts = credit - mark_close
                reason = "EXPIRED_ITM"
                if -pnl_pts > actual_width: pnl_pts = -actual_width

        pnl_usd = pnl_pts * MULT - COMMISSION_PER_TRADE
        trades.append(Trade(
            date=d, width=width, otm_pct=otm_pct,
            profit_target=profit_target, sl_mult=sl_mult,
            short_strike=short_strike, long_strike=long_strike,
            actual_width=actual_width, credit=credit,
            spx_open=spx_open, spx_low=spx_low, spx_close=spx_close, vix=vix,
            pnl_pts=pnl_pts, pnl_usd=pnl_usd, exit_reason=reason,
        ))
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = (df["pnl_usd"] > 0).sum()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    annual = df.groupby("year")["pnl_usd"].sum()
    cum = df.sort_values("date")["pnl_usd"].cumsum()
    drawdown = cum - cum.cummax()
    return {
        "trades": len(df),
        "win_rate": float(wins / len(df)),
        "stops": int((df["exit_reason"] == "STOPPED").sum()),
        "profit_targets": int((df["exit_reason"] == "PROFIT_TARGET").sum()),
        "expired": int((df["exit_reason"] == "EXPIRED").sum()),
        "expired_itm": int((df["exit_reason"] == "EXPIRED_ITM").sum()),
        "total_pnl": float(df["pnl_usd"].sum()),
        "avg_pnl_per_trade": float(df["pnl_usd"].mean()),
        "best_trade": float(df["pnl_usd"].max()),
        "worst_trade": float(df["pnl_usd"].min()),
        "max_drawdown": float(drawdown.min()),
        "annual_avg_1x": float(annual.mean()),
        "annual_min": float(annual.min()),
        "annual_max": float(annual.max()),
        "years_covered": int(df["year"].nunique()),
        "avg_credit": float(df["credit"].mean()),
    }


def run_grid(opts, spx, es_df, filter_mode, label):
    rows = []
    total = len(WIDTHS) * len(OTM_PCTS) * len(PROFIT_TARGETS) * len(SL_MULTS)
    i = 0
    for w in WIDTHS:
        for otm in OTM_PCTS:
            for pt in PROFIT_TARGETS:
                for sl in SL_MULTS:
                    i += 1
                    pt_l = "none" if pt is None else f"{int(pt*100)}%"
                    sl_l = "none" if sl is None else f"{sl}x"
                    trades = simulate(opts, spx, es_df, w, otm, pt, sl, filter_mode=filter_mode)
                    s = summarize(trades)
                    s.update({"width": w, "otm_pct": otm, "profit_target": pt_l,
                              "sl_mult": sl_l, "filter_mode": filter_mode,
                              "period": label})
                    rows.append(s)
                    if s.get("trades", 0) > 0 and i % 10 == 0:
                        _log(f"  [{label}] {i}/{total}: w={w} otm={otm:.4f} PT={pt_l} SL={sl_l} → "
                             f"{s['trades']} trades, win={s['win_rate']:.1%}, "
                             f"annual=${s['annual_avg_1x']:,.0f}, worst=${s['worst_trade']:,.0f}")
    return rows


def main():
    spx, opts, es_df = load_data()
    _log(f"loaded: {len(spx)} SPX days, {len(opts)} options days, {len(es_df)} ES days")

    # Run 1: 2020-2024 with point-in-time filter (PROPER)
    _log("")
    _log("=== Run 1: 2020-2024 with point-in-time filter (PROPER) ===")
    rows1 = run_grid(opts, spx, es_df, filter_mode="point_in_time", label="2020-2024_proper")

    # Run 2: full 12 years with permissive filter (CORROBORATION)
    _log("")
    _log("=== Run 2: 2014-2026 with permissive filter (corroboration only) ===")
    rows2 = run_grid(opts, spx, es_df, filter_mode="permissive", label="2014-2026_permissive")

    all_rows = rows1 + rows2
    out_df = pd.DataFrame(all_rows)
    for c in ("annual_avg_1x", "total_pnl", "win_rate", "worst_trade", "max_drawdown"):
        if c not in out_df.columns: out_df[c] = 0
        out_df[c] = out_df[c].fillna(0)
    out_df.to_csv(OUT / "summary_v2.csv", index=False)

    # Headlines
    print()
    print("=" * 110)
    print("TOP 15 — 2020-2024 POINT-IN-TIME (PROPER), net of $2.60/spread commission, 1× contract")
    print("=" * 110)
    proper = out_df[out_df["filter_mode"] == "point_in_time"].sort_values("annual_avg_1x", ascending=False)
    cols = ["width", "otm_pct", "profit_target", "sl_mult", "trades", "win_rate",
            "stops", "profit_targets", "annual_avg_1x", "worst_trade", "max_drawdown"]
    print(proper[cols].head(15).to_string(index=False))

    print()
    print("=" * 110)
    print("TOP 10 NO-SL CONFIGS (2020-2024 PROPER) — user's thesis")
    print("=" * 110)
    no_sl = proper[proper["sl_mult"] == "none"].head(10)
    print(no_sl[cols].to_string(index=False))

    print()
    print("=" * 110)
    print("CURRENT 50pt × 1.0% × 2x SL EQUIVALENT (baseline): NOT in this grid (widths 5-20)")
    print("Refer to narrow_width_sweep_2026_05_21 results for 50pt baseline (~$14.7K/yr, 90% win)")
    print("=" * 110)

    print()
    print(f"full results: {OUT / 'summary_v2.csv'}")


if __name__ == "__main__":
    main()
