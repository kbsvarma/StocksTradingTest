"""Direction-filter ablation for SPX 0DTE BPS — 2026-05-18

Tests filter ON vs OFF at SL=2.0x AND SL=3.0x, with sub-cuts by year, VIX
bucket, gap-at-open, and es_delta bucket.

Reuses entry_time_sweep machinery: ES 1-min proxy, theta-decay credit at T,
2.0x/3.0x premium SL, expire-or-close exit. Entry = 10:30 ET (live).
"""
from __future__ import annotations
import sys, importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
import databento as db

spec = importlib.util.spec_from_file_location("orig", "/tmp/sl_variant_backtest.py")
orig = importlib.util.module_from_spec(spec)
sys.modules["orig"] = orig
spec.loader.exec_module(orig)

OUT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/direction_filter_ablation_2026_05_18")
OUT.mkdir(parents=True, exist_ok=True)

ENTRY = "10:30"
MIN_CREDIT = 1.20
VIX_MIN, VIX_MAX = 12.0, 25.0
STOP_MULTS = [2.0, 3.0]

OPEN_MIN = 9*60 + 30
CLOSE_MIN = 16*60
T_MIN = 10*60 + 30
ELAPSED = (T_MIN - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN)

ES_CACHE = Path("/tmp/es_1min_combined.parquet")


def load_es_by_day():
    es = pd.read_parquet(ES_CACHE)
    es.index = es.index.tz_convert("America/New_York")
    es["date"] = es.index.date
    es["min"] = es.index.hour*60 + es.index.minute
    rth = es[(es["min"] >= OPEN_MIN) & (es["min"] <= CLOSE_MIN)]
    by_day = {}
    for day, g in rth.groupby("date"):
        s = g.set_index("min")["close"]
        s = s[~s.index.duplicated(keep="first")]
        by_day[day] = s
    return by_day


def build_all_trades(contexts, es_by_day, stop_mult):
    """Build full trade ledger ignoring direction filter. Caller filters later."""
    rt_slip = orig.SLIP_PER_SIDE * 4 * orig.MULT * orig.N_CONTRACTS
    rows = []
    for ctx in contexts:
        if not (VIX_MIN <= ctx.vix <= VIX_MAX):
            continue
        es_minutes = es_by_day.get(ctx.day)
        if es_minutes is None:
            continue
        es_open_idx = es_minutes.index[es_minutes.index >= OPEN_MIN]
        if len(es_open_idx) == 0:
            continue
        es_open = float(es_minutes.loc[es_open_idx[0]])
        avail = es_minutes.index[es_minutes.index >= T_MIN]
        if len(avail) == 0:
            continue
        es_at_T = float(es_minutes.loc[avail[0]])
        credit_at_T = ctx.credit * (1 - ELAPSED) + ctx.spread_close_mark * ELAPSED
        if credit_at_T < MIN_CREDIT:
            continue
        trigger = stop_mult * credit_at_T
        if ctx.spread_peak_mark >= trigger:
            exit_mark = min(ctx.actual_width, trigger * orig.SL_SLIP_MULT)
            pnl_per_spread = credit_at_T - exit_mark
            reason = "stop_premium"
        else:
            exit_mark = ctx.spread_close_mark
            if exit_mark <= 0.05:
                pnl_per_spread = credit_at_T
                reason = "expire"
            else:
                pnl_per_spread = max(-ctx.actual_width, credit_at_T - exit_mark)
                reason = "close"
        net = pnl_per_spread * orig.MULT * orig.N_CONTRACTS - rt_slip
        # Prev-close gap proxy: use spx close[-1] vs spx_open. We'll attach later.
        rows.append({
            "date": pd.Timestamp(ctx.day),
            "vix": ctx.vix,
            "es_open": es_open,
            "es_at_T": es_at_T,
            "es_delta": es_at_T - es_open,
            "spx_open": ctx.spx_open,
            "spx_low": ctx.spx_low,
            "spx_close": ctx.spx_close,
            "short_strike": ctx.short_strike,
            "credit_at_T_$": credit_at_T * orig.MULT,
            "exit_reason": reason,
            "net_pnl_$": net,
            "win": net > 0,
        })
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                "avg_win": 0, "avg_loss": 0, "max_loss": 0, "sharpe": 0}
    pnl = df["net_pnl_$"]
    wins = df[df["win"]]
    losses = df[~df["win"]]
    std = pnl.std()
    sharpe = (pnl.mean() / std * np.sqrt(252)) if std else 0.0
    return {
        "trades": len(df),
        "win_rate": round(df["win"].mean(), 4),
        "total_pnl": round(pnl.sum(), 0),
        "avg_pnl": round(pnl.mean(), 2),
        "avg_win": round(wins["net_pnl_$"].mean(), 2) if not wins.empty else 0,
        "avg_loss": round(losses["net_pnl_$"].mean(), 2) if not losses.empty else 0,
        "max_loss": round(pnl.min(), 0),
        "sharpe": round(sharpe, 3),
    }


def main():
    print("Loading SPX/VIX daily ...")
    spx = orig.load_spx()
    print("Loading option chains ...")
    opts = orig.load_opts_all()
    print("Loading ES 1-min ...")
    es_by_day = load_es_by_day()
    es_days = set(es_by_day.keys())

    print("Building day contexts ...")
    orig.MIN_CREDIT = 0.01
    contexts = []
    for day in sorted(opts):
        if day not in es_days:
            continue
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row = spx.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        ctx = orig.build_day_context(day, opts[day], row)
        if ctx is not None:
            contexts.append(ctx)
    print(f"  {len(contexts)} day contexts")

    # Attach prev-close to spx for gap calculation
    spx_sorted = spx.sort_index()
    prev_close = spx_sorted["spx_close"].shift(1)
    prev_close_map = prev_close.to_dict()

    all_results = {}
    for sl in STOP_MULTS:
        print(f"\n=== Building trade ledger for SL={sl}x ===")
        df = build_all_trades(contexts, es_by_day, sl)
        df["prev_close"] = df["date"].map(lambda d: prev_close_map.get(d, np.nan))
        df["gap"] = df["spx_open"] - df["prev_close"]
        df["abs_gap"] = df["gap"].abs()
        df["year"] = df["date"].dt.year
        df["filter_passes"] = df["es_delta"] >= 0
        all_results[sl] = df
        print(f"  {len(df)} total trades  filter_ON={int(df['filter_passes'].sum())}  filter_blocks={int((~df['filter_passes']).sum())}")
        df.to_csv(OUT / f"all_trades_sl{int(sl*10)}.csv", index=False)

    # ── Headline: filter ON vs OFF ──────────────────────────────
    headline_rows = []
    for sl in STOP_MULTS:
        df = all_results[sl]
        on = df[df["filter_passes"]]
        off = df  # all trades = filter off
        blocked = df[~df["filter_passes"]]
        for label, sub in [("filter_OFF", off), ("filter_ON", on), ("blocked_only", blocked)]:
            m = metrics(sub)
            m["sl_mult"] = sl
            m["cell"] = label
            headline_rows.append(m)
    headline = pd.DataFrame(headline_rows)[
        ["sl_mult", "cell", "trades", "win_rate", "total_pnl", "avg_pnl",
         "avg_win", "avg_loss", "max_loss", "sharpe"]
    ]
    headline.to_csv(OUT / "headline.csv", index=False)
    print("\nHEADLINE:")
    print(headline.to_string(index=False))

    # ── Sub-cuts: by year ──────────────────────────────────────
    year_rows = []
    for sl in STOP_MULTS:
        df = all_results[sl]
        for yr in sorted(df["year"].unique()):
            sub = df[df["year"] == yr]
            sub_on = sub[sub["filter_passes"]]
            for label, s in [("OFF", sub), ("ON", sub_on)]:
                m = metrics(s)
                m["sl_mult"] = sl; m["year"] = int(yr); m["filter"] = label
                year_rows.append(m)
    year_df = pd.DataFrame(year_rows)[
        ["sl_mult", "year", "filter", "trades", "win_rate", "total_pnl", "avg_pnl", "avg_loss", "max_loss", "sharpe"]
    ]
    year_df.to_csv(OUT / "by_year.csv", index=False)

    # ── Sub-cuts: by VIX bucket ────────────────────────────────
    VIX_BUCKETS = [(12, 15), (15, 18), (18, 22), (22, 25)]
    vix_rows = []
    for sl in STOP_MULTS:
        df = all_results[sl]
        for lo, hi in VIX_BUCKETS:
            sub = df[(df["vix"] >= lo) & (df["vix"] < hi)]
            sub_on = sub[sub["filter_passes"]]
            for label, s in [("OFF", sub), ("ON", sub_on)]:
                m = metrics(s)
                m["sl_mult"] = sl; m["vix_bucket"] = f"{lo}-{hi}"; m["filter"] = label
                vix_rows.append(m)
    vix_df = pd.DataFrame(vix_rows)[
        ["sl_mult", "vix_bucket", "filter", "trades", "win_rate", "total_pnl", "avg_pnl", "avg_loss", "max_loss", "sharpe"]
    ]
    vix_df.to_csv(OUT / "by_vix.csv", index=False)

    # ── Sub-cuts: by gap-at-open ───────────────────────────────
    def gap_bucket(g):
        if pd.isna(g): return "unknown"
        a = abs(g)
        if a < 10: return "small_<10"
        if a < 25: return "med_10-25"
        return "large_>25"
    gap_rows = []
    for sl in STOP_MULTS:
        df = all_results[sl].copy()
        df["gap_bkt"] = df["gap"].apply(gap_bucket)
        for bkt in ["small_<10", "med_10-25", "large_>25"]:
            sub = df[df["gap_bkt"] == bkt]
            sub_on = sub[sub["filter_passes"]]
            for label, s in [("OFF", sub), ("ON", sub_on)]:
                m = metrics(s)
                m["sl_mult"] = sl; m["gap_bucket"] = bkt; m["filter"] = label
                gap_rows.append(m)
    gap_df = pd.DataFrame(gap_rows)[
        ["sl_mult", "gap_bucket", "filter", "trades", "win_rate", "total_pnl", "avg_pnl", "avg_loss", "max_loss", "sharpe"]
    ]
    gap_df.to_csv(OUT / "by_gap.csv", index=False)

    # ── Sub-cut: by es_delta bucket (focus on blocked trades) ──
    def delta_bucket(d):
        if d >= 10: return "up_>=10"
        if d >= 0: return "up_0-10"
        if d >= -5: return "down_0-5"
        if d >= -10: return "down_5-10"
        if d >= -20: return "down_10-20"
        return "down_>20"
    delta_rows = []
    for sl in STOP_MULTS:
        df = all_results[sl].copy()
        df["delta_bkt"] = df["es_delta"].apply(delta_bucket)
        for bkt in ["up_>=10", "up_0-10", "down_0-5", "down_5-10", "down_10-20", "down_>20"]:
            sub = df[df["delta_bkt"] == bkt]
            m = metrics(sub)
            m["sl_mult"] = sl; m["delta_bucket"] = bkt
            delta_rows.append(m)
    delta_df = pd.DataFrame(delta_rows)[
        ["sl_mult", "delta_bucket", "trades", "win_rate", "total_pnl", "avg_pnl", "avg_loss", "max_loss", "sharpe"]
    ]
    delta_df.to_csv(OUT / "by_es_delta.csv", index=False)

    print("\nBY YEAR:")
    print(year_df.to_string(index=False))
    print("\nBY VIX:")
    print(vix_df.to_string(index=False))
    print("\nBY GAP:")
    print(gap_df.to_string(index=False))
    print("\nBY ES_DELTA:")
    print(delta_df.to_string(index=False))


if __name__ == "__main__":
    main()
