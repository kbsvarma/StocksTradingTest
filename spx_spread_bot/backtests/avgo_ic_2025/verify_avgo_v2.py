"""AVGO IC verification v2 — handle worthless deep-OTM wings properly.

FIX: When a long-leg wing has NaN bid (no buyers because it's worthless),
treat bid as $0. The option IS worthless; we just close at $0 (or let expire).

This handles the legitimate "deep OTM wing has no liquidity" case.
"""
import re
import time
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
import yfinance as yf

DATA = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 2.5

def log(m): print(m, flush=True)

log("Loading AVGO 2026 data...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA.glob("avgo_cbbo_2026*.parquet")):
    df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
    df["sym_clean"] = df["symbol"].str.strip()
    ext = df["sym_clean"].str.extract(SYM_RE)
    ext.columns = ["root", "yymmdd", "right", "strike_str"]
    df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
    df["right"] = ext["right"]
    df["strike"] = ext["strike_str"].astype(float) / 1000
    df = df.dropna(subset=["expiry", "right", "strike"])
    ds = f.stem.split("_")[-1]
    td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
    for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
        expiry_index[(td, exp, strike, right)] = grp[["bid_px_00", "ask_px_00"]]
        strikes_by_day[td][exp].add(strike)
log(f"Loaded ({time.time()-t0:.0f}s)")

spot_hist = yf.Ticker("AVGO").history(start="2026-01-01", end="2026-06-01", auto_adjust=False)
spot_hist.index = pd.to_datetime(spot_hist.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot_hist.iterrows()}


def find_strike(avail, target, spacing=SPACING):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d_mult in range(1, 15):
        for sign in [+1, -1]:
            t = snapped + sign * d_mult * spacing
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def get_quote_at_or_near(df, ts, window_minutes=10):
    """Get quote at or just before ts, within ±window_minutes."""
    if df is None or df.empty: return None, None
    window = pd.Timedelta(minutes=window_minutes)
    # Prefer last quote at or before ts
    before = df.loc[df.index <= ts]
    if not before.empty:
        last = before.iloc[-1]
        if last.name >= ts - window:
            return float(last["bid_px_00"]), float(last["ask_px_00"])
    # Fall back to first quote after
    after = df.loc[df.index > ts]
    if not after.empty:
        first = after.iloc[0]
        if first.name <= ts + window:
            return float(first["bid_px_00"]), float(first["ask_px_00"])
    return None, None


def simulate_ic_strict_v2(td, otm_pct, wing_pts, exit_hour, exit_min, slippage_per_leg=0.0):
    """V2: handle NaN bid on long wings as $0 (worthless)."""
    if td not in spot_by_date: return {"status": "no_spot"}
    if td not in strikes_by_day[td]: return {"status": "no_0dte"}
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td][td]
    sp = find_strike(avail, spot_entry * (1 - otm_pct))
    sc = find_strike(avail, spot_entry * (1 + otm_pct))
    if sp is None or sc is None: return {"status": "no_shorts"}
    lp = find_strike(avail, sp - wing_pts)
    lc = find_strike(avail, sc + wing_pts)
    if lp is None or lc is None: return {"status": "no_wings"}

    sp_df = expiry_index.get((td, td, sp, "P"))
    sc_df = expiry_index.get((td, td, sc, "C"))
    lp_df = expiry_index.get((td, td, lp, "P"))
    lc_df = expiry_index.get((td, td, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]):
        return {"status": "no_leg_data"}

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=exit_hour, minute=exit_min).astimezone(UTC))

    # ENTRY: at 09:45
    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]):
        return {"status": "no_entry"}

    # Entry: shorts sell at bid (must be valid), longs buy at ask (treat NaN ask = $0.01)
    sp_bid = float(e_sp["bid_px_00"]); sc_bid = float(e_sc["bid_px_00"])
    lp_ask = float(e_lp["ask_px_00"]) if pd.notna(e_lp["ask_px_00"]) else 0.01
    lc_ask = float(e_lc["ask_px_00"]) if pd.notna(e_lc["ask_px_00"]) else 0.01

    if pd.isna(sp_bid) or pd.isna(sc_bid):
        return {"status": "no_short_bids_at_entry"}

    credit = sp_bid + sc_bid - lp_ask - lc_ask - 4 * slippage_per_leg
    if credit < 0.05: return {"status": "thin_credit"}

    stop_debit = credit * 2.0
    pt_debit = credit * 0.5

    # SL/PT walk: scan minute-by-minute, NaN-tolerant
    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    exit_val = None; exit_reason = None
    # Try the walk first
    if not any(s.empty for s in [sp_w, sc_w]):
        # Need shorts to have valid asks (to compute close debit)
        sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        # For longs, fill NaN bid with 0 (worthless option)
        lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"}).copy()
        lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"}).copy()
        lp_r["b_lp"] = lp_r["b_lp"].fillna(0)
        lc_r["b_lc"] = lc_r["b_lc"].fillna(0)
        al = sp_r.join(sc_r, how="inner").join(lp_r[["b_lp"]], how="left").join(lc_r[["b_lc"]], how="left")
        al["b_lp"] = al["b_lp"].fillna(0)
        al["b_lc"] = al["b_lc"].fillna(0)

        # Only rows where short asks are valid
        al = al.dropna(subset=["a_sp", "a_sc"])
        if not al.empty:
            al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
            sl = al[al["debit"] >= stop_debit]
            pt = al[al["debit"] <= pt_debit]
            sl_ts = sl.index.min() if not sl.empty else None
            pt_ts = pt.index.min() if not pt.empty else None
            if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                exit_val = al.loc[sl_ts, "debit"] + 4 * slippage_per_leg
                exit_reason = "stop_loss"
            elif pt_ts is not None:
                exit_val = al.loc[pt_ts, "debit"] + 4 * slippage_per_leg
                exit_reason = "profit_target"

    # FORCED CLOSE: use last quote at or near exit_ts
    if exit_val is None:
        sp_b, sp_a = get_quote_at_or_near(sp_df, exit_ts)
        sc_b, sc_a = get_quote_at_or_near(sc_df, exit_ts)
        lp_b, lp_a = get_quote_at_or_near(lp_df, exit_ts)
        lc_b, lc_a = get_quote_at_or_near(lc_df, exit_ts)

        # Need shorts to have valid asks to close
        if sp_a is None or sc_a is None or pd.isna(sp_a) or pd.isna(sc_a):
            return {"status": "no_short_ask_at_exit"}

        # Long bids: NaN/missing → $0 (option worthless, leave it or sell at min)
        lp_b_safe = lp_b if (lp_b is not None and not pd.isna(lp_b)) else 0
        lc_b_safe = lc_b if (lc_b is not None and not pd.isna(lc_b)) else 0

        exit_val = sp_a + sc_a - lp_b_safe - lc_b_safe + 4 * slippage_per_leg
        exit_reason = "forced_close_v2"

    if exit_val is None or pd.isna(exit_val):
        return {"status": "nan_exit_unexpected"}

    pnl = (credit - exit_val) * 100
    return {
        "status": "ok",
        "trade_date": td, "dow": td.strftime("%a"),
        "sp": sp, "sc": sc, "lp": lp, "lc": lc,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_val": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


def run_all(exit_hour, exit_min, slippage=0.0):
    results = []
    statuses = defaultdict(int)
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        r = simulate_ic_strict_v2(td, 0.005, 25, exit_hour, exit_min, slippage)
        statuses[r["status"]] += 1
        if r["status"] == "ok":
            results.append(r)
    return pd.DataFrame(results), dict(statuses)


log("\n" + "=" * 110)
log("AVGO IC 0.5%/25pt — STRICT v2 with NaN-bid = $0 for long wings")
log("=" * 110)
log(f"{'Exit':<8}{'Slippage':<10}{'Status':<35}{'N OK':<7}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Worst':<9}")
log("-" * 110)

for hour, minute in [(14, 0), (14, 30), (15, 0), (15, 15), (15, 30), (15, 45)]:
    for slip in [0.0, 0.05, 0.10]:
        df, statuses = run_all(hour, minute, slip)
        status_str = ", ".join([f"{k}={v}" for k, v in statuses.items() if k != "ok"])[:33]
        if df.empty:
            log(f"{hour:02d}:{minute:02d}    ${slip:<8.2f}{status_str:<35}0     ")
            continue
        n = len(df); wins = (df["pnl"] > 0).sum()
        log(f"{hour:02d}:{minute:02d}    ${slip:<8.2f}{status_str:<35}{n:<7}{wins/n*100:<6.0f}%${df['pnl'].sum():<+10,.0f}${df['pnl'].mean():<+8.0f}${df['pnl'].min():<+8.0f}")
    log("")

log("\n" + "=" * 110)
log("BEST EXIT for V2 (with $0.05 slippage)")
log("=" * 110)
best_results = []
for hour, minute in [(14, 0), (14, 30), (15, 0), (15, 15), (15, 30), (15, 45)]:
    df, statuses = run_all(hour, minute, 0.05)
    if df.empty: continue
    n = len(df); wins = (df["pnl"] > 0).sum()
    best_results.append({
        "exit": f"{hour:02d}:{minute:02d}", "n": n, "win_rate": wins/n*100,
        "total": df["pnl"].sum(), "avg": df["pnl"].mean(), "worst": df["pnl"].min(),
    })

best_df = pd.DataFrame(best_results).sort_values("total", ascending=False)
log(f"\n{'Exit':<8}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<10}{'Worst':<10}{'Annual (52wk)':<15}")
log("-" * 80)
for _, r in best_df.iterrows():
    annual = r["total"] / 12 * 52
    log(f"{r['exit']:<8}{r['n']:<5}{r['win_rate']:<6.0f}%${r['total']:<+10,.0f}${r['avg']:<+9.0f}${r['worst']:<+9.0f}${annual:<+13,.0f}")
