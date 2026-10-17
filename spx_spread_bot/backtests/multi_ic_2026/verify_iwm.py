"""Independent verification of IWM IFLY 8% wings result.

Tests:
1. Spot-check 5 random trades manually (compare to raw bid/ask)
2. Re-run with FORCED close at 15:45 using actual bid/ask (not intrinsic)
3. Add realistic slippage ($0.05 per leg on entry, $0.05 per leg on exit)
4. Random partition cross-validation (split sample randomly, verify each half)
5. Check for data gaps / silently skipped bad days
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

DATA = Path(__file__).parent / "data" / "iwm_wide"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 1.0

def log(m): print(m, flush=True)


log("Loading IWM wide data...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
for f in files:
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

spot_hist = yf.Ticker("IWM").history(start="2024-01-01", end="2026-06-01", auto_adjust=False)
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


def at_or_before(df, ts):
    """Get the last quote at or before timestamp."""
    if df is None or df.empty: return None
    sub = df.loc[df.index <= ts]
    return None if sub.empty else sub.iloc[-1]


def simulate_iron_fly_strict(td, wing_pct, use_real_exit=True, slippage_per_leg=0.0):
    """STRICT simulator with options:
    - use_real_exit=True: use bid/ask at 15:45 to close (NOT intrinsic)
    - slippage_per_leg: subtract from credit on entry, add to debit on exit
    """
    if td not in spot_by_date: return None
    if td not in strikes_by_day[td]: return None
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td][td]
    atm = find_strike(avail, spot_entry)
    if atm is None: return None
    wing = spot_entry * wing_pct
    lp = find_strike(avail, atm - wing); lc = find_strike(avail, atm + wing)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, td, atm, "P"))
    sc_df = expiry_index.get((td, td, atm, "C"))
    lp_df = expiry_index.get((td, td, lp, "P"))
    lc_df = expiry_index.get((td, td, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=15, minute=45).astimezone(UTC))

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]): return None

    # ENTRY: collect bid on shorts, pay ask on longs, MINUS slippage per leg
    credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
              - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
              - 4 * slippage_per_leg)
    if credit < 0.05: return None

    stop_debit = credit * 2.0
    pt_debit = credit * 0.5

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]
    if any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]): return None

    sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
    sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
    lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
    lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
    al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
    if al.empty: return None

    al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
    sl = al[al["debit"] >= stop_debit]; pt = al[al["debit"] <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = al.loc[sl_ts, "debit"] + 4 * slippage_per_leg
        exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = al.loc[pt_ts, "debit"] + 4 * slippage_per_leg
        exit_reason = "profit_target"
    else:
        # FORCED CLOSE AT 15:45 — use REAL quotes (not intrinsic)
        if use_real_exit:
            # Last row in aligned is at or near 15:45
            last_row = al.iloc[-1]
            exit_val = last_row["debit"] + 4 * slippage_per_leg
            exit_reason = "forced_close_real"
        else:
            # OLD MODEL: intrinsic at spot close
            sp_int = max(0, atm - spot_c); lp_int = max(0, lp - spot_c)
            sc_int = max(0, spot_c - atm); lc_int = max(0, spot_c - lc)
            exit_val = sp_int + sc_int - lp_int - lc_int
            exit_reason = "expiry_intrinsic"

    pnl = (credit - exit_val) * 100
    return {
        "trade_date": td, "dow": td.strftime("%a"), "year": td.year,
        "atm": atm, "lp": lp, "lc": lc,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_val": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


def run_all(wing_pct, use_real_exit=True, slippage=0.0):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        r = simulate_iron_fly_strict(td, wing_pct, use_real_exit, slippage)
        if r: trades.append(r)
    return pd.DataFrame(trades)


def stats(df, label):
    if df.empty: return
    n = len(df); wins = (df["pnl"] > 0).sum()
    total = df["pnl"].sum()
    log(f"  {label:<55}N={n:<5}Win={wins/n*100:.0f}%  Total=${total:+,.0f}  Avg=${df['pnl'].mean():+.0f}  Worst=${df['pnl'].min():+.0f}")


# ─── VERIFICATION ──────────────────────────────────────────────────────────
log("\n" + "=" * 110)
log("VERIFICATION: IWM IFLY 8% wings — checking previous $30,746 result")
log("=" * 110)

# Variant A: original (intrinsic at close, no slippage) — should match prior result
log("\nA. Original model (intrinsic settlement, no slippage):")
df_a = run_all(0.08, use_real_exit=False, slippage=0.0)
stats(df_a, "Original (intrinsic settlement, no slip)")

# Variant B: REAL exit (use 15:45 quote, not intrinsic)
log("\nB. Same but FORCED CLOSE at 15:45 using REAL bid/ask (not intrinsic):")
df_b = run_all(0.08, use_real_exit=True, slippage=0.0)
stats(df_b, "Real exit at 15:45 (no slip)")
exit_dist = df_b["exit_reason"].value_counts()
log(f"     Exit reasons: {dict(exit_dist)}")

# Variant C: Real exit + small slippage ($0.05 per leg = $0.20 round trip)
log("\nC. Real exit + $0.05 slippage per leg (typical retail fill):")
df_c = run_all(0.08, use_real_exit=True, slippage=0.05)
stats(df_c, "Real exit + $0.05/leg slip")

# Variant D: Real exit + larger slippage ($0.10 per leg = $0.40 round trip)
log("\nD. Real exit + $0.10 slippage per leg (conservative):")
df_d = run_all(0.08, use_real_exit=True, slippage=0.10)
stats(df_d, "Real exit + $0.10/leg slip")

log("\n" + "=" * 110)
log("DELTA FROM ORIGINAL")
log("=" * 110)
orig = df_a["pnl"].sum()
log(f"Original (claimed): ${orig:+,.0f}")
log(f"With real exit:     ${df_b['pnl'].sum():+,.0f}  ({(df_b['pnl'].sum()-orig)/orig*100:+.1f}%)")
log(f"+ $0.05/leg slip:   ${df_c['pnl'].sum():+,.0f}  ({(df_c['pnl'].sum()-orig)/orig*100:+.1f}%)")
log(f"+ $0.10/leg slip:   ${df_d['pnl'].sum():+,.0f}  ({(df_d['pnl'].sum()-orig)/orig*100:+.1f}%)")

# ─── SPOT CHECK 5 RANDOM TRADES ────────────────────────────────────────────
log("\n" + "=" * 110)
log("SPOT-CHECK: 5 random trades from Variant B (most realistic)")
log("=" * 110)
random.seed(42)
df_b_sorted = df_b.sort_values("trade_date").reset_index(drop=True)
sample_idx = random.sample(range(len(df_b_sorted)), 5)
for idx in sample_idx:
    r = df_b_sorted.iloc[idx]
    td = r["trade_date"]
    log(f"\nTrade {idx}: {td} ({r['dow']}), ATM={r['atm']}, wings={r['lp']}/{r['lc']}")
    log(f"  Credit collected: ${r['credit']*100:.0f}")
    log(f"  Exit value: ${r['exit_val']*100:.0f}  reason={r['exit_reason']}")
    log(f"  P&L: ${r['pnl']:+.0f}")

    # Verify by re-fetching the actual quotes
    sp_df = expiry_index.get((td, td, r["atm"], "P"))
    sc_df = expiry_index.get((td, td, r["atm"], "C"))
    lp_df = expiry_index.get((td, td, r["lp"], "P"))
    lc_df = expiry_index.get((td, td, r["lc"], "C"))
    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=45).astimezone(UTC))
    if sp_df is not None:
        e_sp = at_or_after(sp_df, entry_ts)
        e_sc = at_or_after(sc_df, entry_ts)
        e_lp = at_or_after(lp_df, entry_ts)
        e_lc = at_or_after(lc_df, entry_ts)
        if all(e is not None for e in [e_sp, e_sc, e_lp, e_lc]):
            manual_credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
                             - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"]))
            log(f"  Manual recheck — credit at 09:45: ${manual_credit*100:.0f}  (matches simulator: {abs(manual_credit*100 - r['credit']*100) < 1})")

# ─── DATA COMPLETENESS CHECK ───────────────────────────────────────────────
log("\n" + "=" * 110)
log("DATA COMPLETENESS — are bad days being silently skipped?")
log("=" * 110)
# Count days in spot history vs days simulated
sim_dates = set(df_b["trade_date"])
expected = set(pd.Timestamp(d).date() for d in spot_hist.index
               if pd.Timestamp(d).date().year in [2024, 2025, 2026])
missing = expected - sim_dates
log(f"  Trading days in spot history (2024-2026): {len(expected)}")
log(f"  Trading days simulated: {len(sim_dates)}")
log(f"  Missing (no IWM data or no 0DTE that day): {len(missing)}")

# Check if missing days were big-move days
big_move_days = []
for d in missing:
    if d in spot_by_date:
        row = spot_by_date[d]
        mv = (float(row["Close"]) - float(row["Open"])) / float(row["Open"]) * 100
        if abs(mv) > 2:
            big_move_days.append((d, mv))
log(f"  Missing days with >2% move: {len(big_move_days)}")
if big_move_days:
    log("  These big-move days were skipped (could be bias!):")
    for d, mv in big_move_days[:10]:
        log(f"    {d}: {mv:+.1f}%")

# ─── RANDOM PARTITION CROSS-VALIDATION ────────────────────────────────────
log("\n" + "=" * 110)
log("CROSS-VALIDATION: Random split into 2 halves")
log("=" * 110)
random.seed(123)
indices = list(range(len(df_b)))
random.shuffle(indices)
mid = len(indices) // 2
half_a = df_b.iloc[indices[:mid]]
half_b = df_b.iloc[indices[mid:]]
log(f"\n  Half A ({len(half_a)} trades):  Win={(half_a['pnl']>0).mean()*100:.0f}%  Total=${half_a['pnl'].sum():+,.0f}  Avg=${half_a['pnl'].mean():+.0f}")
log(f"  Half B ({len(half_b)} trades):  Win={(half_b['pnl']>0).mean()*100:.0f}%  Total=${half_b['pnl'].sum():+,.0f}  Avg=${half_b['pnl'].mean():+.0f}")

# ─── BIG WINNERS / LOSERS DISTRIBUTION ────────────────────────────────────
log("\n" + "=" * 110)
log("P&L DISTRIBUTION SHAPE — sanity check")
log("=" * 110)
bins = [(-9999, -300), (-300, -100), (-100, 0), (0, 50), (50, 150), (150, 300), (300, 9999)]
for lo, hi in bins:
    n = ((df_b["pnl"] >= lo) & (df_b["pnl"] < hi)).sum()
    log(f"  ${lo:>+5,} to ${hi:>+5,}: {n:>4} ({n/len(df_b)*100:.1f}%)")

# ─── SUMMARY ───────────────────────────────────────────────────────────────
log("\n" + "=" * 110)
log("VERIFICATION SUMMARY")
log("=" * 110)
log(f"Previously claimed (Original):   ${df_a['pnl'].sum():+,.0f}  ({(df_a['pnl']>0).mean()*100:.0f}% win)")
log(f"With real-exit fix:              ${df_b['pnl'].sum():+,.0f}  ({(df_b['pnl']>0).mean()*100:.0f}% win)")
log(f"With realistic slippage ($0.05): ${df_c['pnl'].sum():+,.0f}  ({(df_c['pnl']>0).mean()*100:.0f}% win)")
log(f"Conservative slippage ($0.10):   ${df_d['pnl'].sum():+,.0f}  ({(df_d['pnl']>0).mean()*100:.0f}% win)")
log(f"\nAnnualized (2.5 years):")
log(f"  Best case:       ${df_a['pnl'].sum()/2.5:+,.0f}/year")
log(f"  Realistic:       ${df_c['pnl'].sum()/2.5:+,.0f}/year")
log(f"  Conservative:    ${df_d['pnl'].sum()/2.5:+,.0f}/year")
