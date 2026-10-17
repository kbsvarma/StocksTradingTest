"""Iron Condor vs BPS backtest — 2026-05-18

Tests 4 variants on 12yr SPX 0DTE data:
  A: BPS only, direction filter ON (current production)
  B: Pure IC, no direction filter
  C: Hybrid — BPS if SPX>=open, else IC
  D: IC with direction filter ON (sanity)

Reuses /tmp/sl_variant_backtest.py (puts) + extends to load calls.
Entry 10:30 ET, 50pt wide each side, 0.75% OTM each side, 2.0x premium SL.
"""
from __future__ import annotations
import sys, importlib.util, re, warnings
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
import numpy as np
import pandas as pd
import databento as db

warnings.filterwarnings("ignore")

spec = importlib.util.spec_from_file_location("orig", "/tmp/sl_variant_backtest.py")
orig = importlib.util.module_from_spec(spec)
sys.modules["orig"] = orig
spec.loader.exec_module(orig)

OUT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/iron_condor_vs_bps_2026_05_18")
OUT.mkdir(parents=True, exist_ok=True)

# Constants — match production
WIDTH = 50
OTM_PCT = 0.0075
N_CONTRACTS = 1
MIN_CREDIT_BPS = 1.20      # per spread, $ (puts side only)
MIN_CREDIT_IC = 1.20       # sum across both sides
MULT = 100.0
SLIP_PER_SIDE = 0.05       # $0.05 per leg per side
VIX_MIN, VIX_MAX = 12.0, 25.0
STOP_MULT = 2.0
SL_SLIP_MULT = 1.10

ENTRY_MIN = 10*60 + 30
OPEN_MIN = 9*60 + 30
CLOSE_MIN = 16*60
ELAPSED = (ENTRY_MIN - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN)

ES_CACHE = Path("/tmp/es_1min_combined.parquet")


# ── Extended option loader: pull BOTH puts AND calls ──────────────────
def load_opts_puts_and_calls():
    BASE = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/data/databento")
    DIRS = [
        BASE / "spxw_opt_2014_2015",
        BASE / "spxw_opt_2016_2018",
        BASE / "spxw_opt_2019",
        BASE / "spxw_opt_2020_2022",
        BASE / "spxw_opt",
        BASE / "spxw_opt_2025_2026",
    ]
    out_puts = {}
    out_calls = {}
    files = []
    for d in DIRS:
        if d.exists():
            files.extend(sorted(d.glob("opra-pillar-*.ohlcv-1d.dbn.zst")))
    print(f"  Loading {len(files)} option-chain files (puts+calls)...")
    for fp in files:
        try:
            df = db.DBNStore.from_file(fp).to_df()
        except Exception:
            continue
        if df.empty:
            continue
        df["parsed"] = df["symbol"].apply(orig.parse_symbol)
        df = df[df["parsed"].notna()].copy()
        if df.empty:
            continue
        df["expiry"] = df["parsed"].apply(lambda x: x[1])
        df["right"]  = df["parsed"].apply(lambda x: x[2])
        df["strike"] = df["parsed"].apply(lambda x: x[3])
        try:
            td = datetime.strptime(fp.name.split("-")[2].split(".")[0], "%Y%m%d").date()
        except ValueError:
            continue
        same = df[df["expiry"] == td]
        puts = same[same["right"] == "P"][["strike","open","high","low","close"]]
        calls = same[same["right"] == "C"][["strike","open","high","low","close"]]
        if not puts.empty:
            out_puts[td] = puts.reset_index(drop=True)
        if not calls.empty:
            out_calls[td] = calls.reset_index(drop=True)
    common = set(out_puts) & set(out_calls)
    print(f"  Puts: {len(out_puts)} days  Calls: {len(out_calls)} days  Common: {len(common)}")
    return out_puts, out_calls


# ── Per-side context (works for both put-side and call-side) ──────────
@dataclass
class SideContext:
    short_strike: float
    long_strike: float
    actual_width: float
    credit: float            # per spread, $
    spread_close_mark: float
    spread_peak_mark: float


def build_put_side(opts_d: pd.DataFrame, spx_open: float):
    target_short = spx_open * (1.0 - OTM_PCT)
    opts_d = opts_d.copy()
    opts_d["d"] = (opts_d["strike"] - target_short).abs()
    sr = opts_d.loc[opts_d["d"].idxmin()]
    short_strike = float(sr["strike"])
    long_target = short_strike - WIDTH
    exact = opts_d[opts_d["strike"] == long_target]
    if not exact.empty:
        lr = exact.iloc[0]
    else:
        below = opts_d[opts_d["strike"] < short_strike]
        if below.empty: return None
        below = below.copy(); below["d"] = (below["strike"] - long_target).abs()
        lr = below.loc[below["d"].idxmin()]
    long_strike = float(lr["strike"])
    actual_width = short_strike - long_strike
    if actual_width <= 0: return None
    so, sc = float(sr["open"]), float(sr["close"])
    sh = float(sr["high"]) if not pd.isna(sr["high"]) else so
    lo, lc = float(lr["open"]), float(lr["close"])
    ll = float(lr["low"]) if not pd.isna(lr["low"]) else lc
    credit = so - lo
    if credit <= 0: return None
    spread_close_mark = max(0.0, sc - lc)
    worst_peak = max(0.0, sh - ll)
    spread_peak_mark = min(actual_width, max(spread_close_mark, 0.70 * worst_peak))
    return SideContext(short_strike, long_strike, actual_width, credit, spread_close_mark, spread_peak_mark)


def build_call_side(opts_d: pd.DataFrame, spx_open: float):
    """Bear call spread: short call ~0.75% OTM above spot, long call 50pt higher."""
    target_short = spx_open * (1.0 + OTM_PCT)
    opts_d = opts_d.copy()
    opts_d["d"] = (opts_d["strike"] - target_short).abs()
    sr = opts_d.loc[opts_d["d"].idxmin()]
    short_strike = float(sr["strike"])
    long_target = short_strike + WIDTH
    exact = opts_d[opts_d["strike"] == long_target]
    if not exact.empty:
        lr = exact.iloc[0]
    else:
        above = opts_d[opts_d["strike"] > short_strike]
        if above.empty: return None
        above = above.copy(); above["d"] = (above["strike"] - long_target).abs()
        lr = above.loc[above["d"].idxmin()]
    long_strike = float(lr["strike"])
    actual_width = long_strike - short_strike
    if actual_width <= 0: return None
    so, sc = float(sr["open"]), float(sr["close"])
    sh = float(sr["high"]) if not pd.isna(sr["high"]) else so
    lo, lc = float(lr["open"]), float(lr["close"])
    ll = float(lr["low"]) if not pd.isna(lr["low"]) else lc
    credit = so - lo  # short call premium - long call premium
    if credit <= 0: return None
    spread_close_mark = max(0.0, sc - lc)
    worst_peak = max(0.0, sh - ll)
    spread_peak_mark = min(actual_width, max(spread_close_mark, 0.70 * worst_peak))
    return SideContext(short_strike, long_strike, actual_width, credit, spread_close_mark, spread_peak_mark)


# ── ES intraday loader (for direction filter + entry mark) ────────────
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


# ── Side simulation: returns pnl_per_spread and reason ────────────────
def simulate_side(side: SideContext, stop_mult: float):
    """Apply theta-decay credit-at-T and premium SL. Returns (pnl_$_per_spread, reason)."""
    credit_at_T = side.credit * (1 - ELAPSED) + side.spread_close_mark * ELAPSED
    if credit_at_T <= 0:
        return 0.0, "no_credit", credit_at_T
    trigger = stop_mult * credit_at_T
    if side.spread_peak_mark >= trigger:
        exit_mark = min(side.actual_width, trigger * SL_SLIP_MULT)
        pnl = credit_at_T - exit_mark
        return pnl, "stop", credit_at_T
    exit_mark = side.spread_close_mark
    if exit_mark <= 0.05:
        return credit_at_T, "expire", credit_at_T
    pnl = max(-side.actual_width, credit_at_T - exit_mark)
    return pnl, "close", credit_at_T


# ── Main backtest loop ────────────────────────────────────────────────
def main():
    print("Loading SPX/VIX ...")
    spx = orig.load_spx()
    print("Loading option chains (puts + calls) ...")
    puts_by_day, calls_by_day = load_opts_puts_and_calls()
    print("Loading ES 1-min ...")
    es_by_day = load_es_by_day()
    es_days = set(es_by_day.keys())

    days = sorted(set(puts_by_day) & set(calls_by_day) & es_days)
    print(f"  {len(days)} trading days with full data")

    # Slippage per single spread (2 legs * 2 sides * $0.05) = $0.20 = $20
    side_slip = SLIP_PER_SIDE * 4 * MULT * N_CONTRACTS  # $20 per spread side

    rows_per_day = []
    for day in days:
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row = spx.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        vix = float(row["vix_close"]) if not pd.isna(row["vix_close"]) else 20.0
        if not (VIX_MIN <= vix <= VIX_MAX):
            continue
        spx_open = float(row["spx_open"])
        spx_close = float(row["spx_close"])

        es_minutes = es_by_day.get(day)
        if es_minutes is None: continue
        es_open_idx = es_minutes.index[es_minutes.index >= OPEN_MIN]
        if len(es_open_idx) == 0: continue
        es_open = float(es_minutes.loc[es_open_idx[0]])
        avail = es_minutes.index[es_minutes.index >= ENTRY_MIN]
        if len(avail) == 0: continue
        es_at_T = float(es_minutes.loc[avail[0]])
        direction_passes = (es_at_T - es_open) >= 0

        put_side = build_put_side(puts_by_day[day], spx_open)
        call_side = build_call_side(calls_by_day[day], spx_open)
        if put_side is None or call_side is None:
            continue

        # Simulate both sides independently
        p_pnl, p_reason, p_credit_T = simulate_side(put_side, STOP_MULT)
        c_pnl, c_reason, c_credit_T = simulate_side(call_side, STOP_MULT)

        # Per-trade net (in $) for each side: pnl * MULT * N - side_slip
        p_net = p_pnl * MULT * N_CONTRACTS - side_slip
        c_net = c_pnl * MULT * N_CONTRACTS - side_slip

        ic_total_credit_T = p_credit_T + c_credit_T

        rows_per_day.append({
            "date": pd.Timestamp(day),
            "year": ts.year,
            "vix": vix,
            "spx_open": spx_open,
            "spx_close": spx_close,
            "es_open": es_open,
            "es_at_T": es_at_T,
            "es_delta": es_at_T - es_open,
            "direction_passes": direction_passes,
            "put_short_strike": put_side.short_strike,
            "call_short_strike": call_side.short_strike,
            "put_credit_T": p_credit_T,
            "call_credit_T": c_credit_T,
            "ic_credit_T": ic_total_credit_T,
            "put_pnl_$": p_net,
            "call_pnl_$": c_net,
            "put_reason": p_reason,
            "call_reason": c_reason,
            "put_close_mark": put_side.spread_close_mark,
            "call_close_mark": call_side.spread_close_mark,
            "put_peak_mark": put_side.spread_peak_mark,
            "call_peak_mark": call_side.spread_peak_mark,
        })

    df = pd.DataFrame(rows_per_day)
    print(f"\n{len(df)} candidate trading days (VIX 12-25 + data present)")
    df.to_csv(OUT / "raw_day_ledger.csv", index=False)

    # ── Build variants ────────────────────────────────────────────────
    # Variant A: BPS only, direction filter ON, min_credit_T >= 1.20 (put side)
    vA = df[df["direction_passes"] & (df["put_credit_T"] >= MIN_CREDIT_BPS)].copy()
    vA["net_pnl_$"] = vA["put_pnl_$"]
    vA["win"] = vA["net_pnl_$"] > 0
    vA["sl_fire"] = vA["put_reason"] == "stop"
    vA.to_csv(OUT / "variantA_bps_filtered.csv", index=False)

    # Variant B: Pure IC, NO direction filter, min_credit_T (sum) >= 1.20
    vB = df[df["ic_credit_T"] >= MIN_CREDIT_IC].copy()
    vB["net_pnl_$"] = vB["put_pnl_$"] + vB["call_pnl_$"]
    vB["win"] = vB["net_pnl_$"] > 0
    vB["put_sl_fire"] = vB["put_reason"] == "stop"
    vB["call_sl_fire"] = vB["call_reason"] == "stop"
    vB.to_csv(OUT / "variantB_ic_no_filter.csv", index=False)

    # Variant C: Hybrid — BPS if direction_passes (with BPS credit), else IC (with IC credit)
    parts = []
    a_mask = df["direction_passes"] & (df["put_credit_T"] >= MIN_CREDIT_BPS)
    a_part = df[a_mask].copy()
    a_part["net_pnl_$"] = a_part["put_pnl_$"]
    a_part["leg_type"] = "BPS"
    a_part["put_sl_fire"] = a_part["put_reason"] == "stop"
    a_part["call_sl_fire"] = False
    parts.append(a_part)
    b_mask = (~df["direction_passes"]) & (df["ic_credit_T"] >= MIN_CREDIT_IC)
    b_part = df[b_mask].copy()
    b_part["net_pnl_$"] = b_part["put_pnl_$"] + b_part["call_pnl_$"]
    b_part["leg_type"] = "IC"
    b_part["put_sl_fire"] = b_part["put_reason"] == "stop"
    b_part["call_sl_fire"] = b_part["call_reason"] == "stop"
    parts.append(b_part)
    vC = pd.concat(parts).sort_values("date").reset_index(drop=True)
    vC["win"] = vC["net_pnl_$"] > 0
    vC.to_csv(OUT / "variantC_hybrid.csv", index=False)

    # Variant D: IC with direction filter ON
    vD = df[df["direction_passes"] & (df["ic_credit_T"] >= MIN_CREDIT_IC)].copy()
    vD["net_pnl_$"] = vD["put_pnl_$"] + vD["call_pnl_$"]
    vD["win"] = vD["net_pnl_$"] > 0
    vD["put_sl_fire"] = vD["put_reason"] == "stop"
    vD["call_sl_fire"] = vD["call_reason"] == "stop"
    vD.to_csv(OUT / "variantD_ic_filtered.csv", index=False)

    # ── Metrics ───────────────────────────────────────────────────────
    def metrics(name, dfv, put_sl_col=None, call_sl_col=None):
        if dfv.empty:
            return {"variant": name, "trades": 0}
        pnl = dfv["net_pnl_$"]
        wins = dfv[dfv["win"]]
        losses = dfv[~dfv["win"]]
        std = pnl.std()
        sharpe = (pnl.mean() / std * np.sqrt(252)) if std else 0.0
        out = {
            "variant": name,
            "trades": len(dfv),
            "win_rate": round(dfv["win"].mean(), 4),
            "total_pnl_$": round(pnl.sum(), 0),
            "avg_pnl_$": round(pnl.mean(), 2),
            "avg_win_$": round(wins["net_pnl_$"].mean(), 2) if not wins.empty else 0,
            "avg_loss_$": round(losses["net_pnl_$"].mean(), 2) if not losses.empty else 0,
            "max_loss_$": round(pnl.min(), 0),
            "max_win_$": round(pnl.max(), 0),
            "stddev_$": round(std, 2),
            "sharpe": round(sharpe, 3),
        }
        if put_sl_col and put_sl_col in dfv.columns:
            out["put_sl_fires"] = int(dfv[put_sl_col].sum())
        if call_sl_col and call_sl_col in dfv.columns:
            out["call_sl_fires"] = int(dfv[call_sl_col].sum())
        return out

    summary = pd.DataFrame([
        metrics("A_BPS_filtered", vA, put_sl_col="sl_fire"),
        metrics("B_IC_no_filter", vB, put_sl_col="put_sl_fire", call_sl_col="call_sl_fire"),
        metrics("C_Hybrid", vC, put_sl_col="put_sl_fire", call_sl_col="call_sl_fire"),
        metrics("D_IC_filtered", vD, put_sl_col="put_sl_fire", call_sl_col="call_sl_fire"),
    ])
    summary.to_csv(OUT / "summary.csv", index=False)
    print("\nSUMMARY:")
    print(summary.to_string(index=False))

    # ── Year-by-year for each variant ─────────────────────────────────
    yr_rows = []
    for name, dfv in [("A", vA), ("B", vB), ("C", vC), ("D", vD)]:
        for yr in sorted(dfv["year"].unique()):
            sub = dfv[dfv["year"] == yr]
            yr_rows.append({
                "variant": name,
                "year": int(yr),
                "trades": len(sub),
                "win_rate": round(sub["win"].mean(), 4),
                "total_pnl_$": round(sub["net_pnl_$"].sum(), 0),
                "max_loss_$": round(sub["net_pnl_$"].min(), 0),
                "max_win_$": round(sub["net_pnl_$"].max(), 0),
            })
    yr_df = pd.DataFrame(yr_rows)
    yr_df.to_csv(OUT / "by_year.csv", index=False)
    print("\nBY YEAR (pivoted on total_pnl):")
    pivot = yr_df.pivot(index="year", columns="variant", values="total_pnl_$").fillna(0)
    print(pivot.to_string())

    # ── Analyses ──────────────────────────────────────────────────────
    # 1) IC call-side tail risk: how many max-loss days on call side
    call_max_loss_threshold = -0.85 * WIDTH * MULT  # -$4,250 = approx max loss on side after slip
    ic_full = df[df["ic_credit_T"] >= MIN_CREDIT_IC].copy()
    call_blowouts = ic_full[ic_full["call_pnl_$"] <= call_max_loss_threshold].copy()
    call_blowouts["spx_move_pct"] = (call_blowouts["spx_close"] / call_blowouts["spx_open"] - 1) * 100
    call_blowouts.to_csv(OUT / "ic_call_blowout_days.csv", index=False)
    print(f"\nCALL-SIDE BLOWOUTS (pnl <= {call_max_loss_threshold}): {len(call_blowouts)} days")
    if len(call_blowouts):
        print(f"  Avg SPX move on blowout days: {call_blowouts['spx_move_pct'].mean():.2f}%")
        print(f"  Max SPX move on blowout days: {call_blowouts['spx_move_pct'].max():.2f}%")

    put_blowouts = ic_full[ic_full["put_pnl_$"] <= call_max_loss_threshold].copy()
    print(f"PUT-SIDE BLOWOUTS:  {len(put_blowouts)} days")

    # 2) Days direction filter skips: what would IC do?
    blocked = df[(~df["direction_passes"]) & (df["ic_credit_T"] >= MIN_CREDIT_IC)].copy()
    blocked["ic_pnl_$"] = blocked["put_pnl_$"] + blocked["call_pnl_$"]
    blocked["ic_win"] = blocked["ic_pnl_$"] > 0
    blocked.to_csv(OUT / "ic_on_filter_blocked_days.csv", index=False)
    print(f"\nDIRECTION-FILTER-BLOCKED DAYS (IC would take {len(blocked)}):")
    if len(blocked):
        print(f"  Win rate: {blocked['ic_win'].mean():.2%}")
        print(f"  Total P&L: ${blocked['ic_pnl_$'].sum():.0f}")
        print(f"  Avg P&L:   ${blocked['ic_pnl_$'].mean():.2f}")
        print(f"  Max loss:  ${blocked['ic_pnl_$'].min():.0f}")

    print(f"\nDone. Outputs in {OUT}")
    return summary, yr_df, call_blowouts, blocked


if __name__ == "__main__":
    main()
