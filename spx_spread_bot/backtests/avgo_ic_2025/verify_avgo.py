"""Strict verification of AVGO IC 0.5% OTM / 25pt wings result.

Same fix as IWM verification:
- Use REAL bid/ask at 15:45 (not intrinsic at 4PM close)
- Add realistic slippage
- Spot-check trades
- Cross-validation
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

# Load 2026 data only (M/W/F structure)
log("Loading AVGO 2026 data...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
files = sorted(DATA.glob("avgo_cbbo_2026*.parquet"))
log(f"  Files: {len(files)}")
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
log(f"Loaded ({time.time()-t0:.0f}s, {len(strikes_by_day)} dates)")

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


def simulate_ic_strict(td, otm_pct, wing_pts, use_real_exit=True, slippage_per_leg=0.0):
    if td not in spot_by_date: return None
    if td not in strikes_by_day[td]: return None
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td][td]
    sp = find_strike(avail, spot_entry * (1 - otm_pct))
    sc = find_strike(avail, spot_entry * (1 + otm_pct))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing_pts)
    lc = find_strike(avail, sc + wing_pts)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, td, sp, "P"))
    sc_df = expiry_index.get((td, td, sc, "C"))
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
        if use_real_exit:
            last_row = al.iloc[-1]
            exit_val = last_row["debit"] + 4 * slippage_per_leg
            exit_reason = "forced_close_real"
        else:
            sp_int = max(0, sp - spot_c); lp_int = max(0, lp - spot_c)
            sc_int = max(0, spot_c - sc); lc_int = max(0, spot_c - lc)
            exit_val = sp_int + sc_int - lp_int - lc_int
            exit_reason = "expiry_intrinsic"

    pnl = (credit - exit_val) * 100
    return {
        "trade_date": td, "dow": td.strftime("%a"),
        "sp": sp, "sc": sc, "lp": lp, "lc": lc,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_val": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


def run_all(otm_pct, wing_pts, use_real_exit=True, slippage=0.0):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue  # M/W/F only
        r = simulate_ic_strict(td, otm_pct, wing_pts, use_real_exit, slippage)
        if r: trades.append(r)
    return pd.DataFrame(trades)


def stats(df, label):
    if df.empty:
        log(f"  {label}: NO TRADES")
        return
    n = len(df); wins = (df["pnl"] > 0).sum()
    valid = df[df["pnl"].notna()]
    n_valid = len(valid); wins_v = (valid["pnl"] > 0).sum()
    total = valid["pnl"].sum()
    log(f"  {label:<55}N={n:<5}(valid:{n_valid})  Win={wins_v/n_valid*100:.0f}%  Total=${total:+,.0f}  Avg=${valid['pnl'].mean():+.0f}  Worst=${valid['pnl'].min():+.0f}")


log("\n" + "=" * 110)
log("VERIFICATION: AVGO IC 0.5% OTM / 25pt wings — checking $6,533 / $189 trade claim")
log("=" * 110)

# A. Original (intrinsic settlement, no slippage)
log("\nA. Original model (intrinsic settlement, no slippage):")
df_a = run_all(0.005, 25, use_real_exit=False, slippage=0.0)
stats(df_a, "Original (intrinsic, no slip)")

# B. Real exit at 15:45 (no slippage)
log("\nB. Real exit at 15:45 using actual bid/ask (no slippage):")
df_b = run_all(0.005, 25, use_real_exit=True, slippage=0.0)
stats(df_b, "Real exit (no slip)")
if not df_b.empty:
    exit_dist = df_b["exit_reason"].value_counts()
    log(f"     Exit reasons: {dict(exit_dist)}")

# C. Real exit + $0.05/leg slippage
log("\nC. Real exit + $0.05 slippage per leg:")
df_c = run_all(0.005, 25, use_real_exit=True, slippage=0.05)
stats(df_c, "Real exit + $0.05/leg slip")

# D. Real exit + $0.10/leg slippage
log("\nD. Real exit + $0.10 slippage per leg (conservative):")
df_d = run_all(0.005, 25, use_real_exit=True, slippage=0.10)
stats(df_d, "Real exit + $0.10/leg slip")

# Summary
log("\n" + "=" * 110)
log("DELTA FROM ORIGINAL")
log("=" * 110)
orig = df_a["pnl"].sum() if not df_a.empty else 0
def pct(x, base):
    if base == 0: return "n/a"
    return f"{(x-base)/abs(base)*100:+.1f}%"
log(f"Original (claimed): ${orig:+,.0f}")
if not df_b.empty:
    log(f"With real exit:     ${df_b['pnl'].sum():+,.0f}  ({pct(df_b['pnl'].sum(), orig)})")
if not df_c.empty:
    log(f"+ $0.05/leg slip:   ${df_c['pnl'].sum():+,.0f}  ({pct(df_c['pnl'].sum(), orig)})")
if not df_d.empty:
    log(f"+ $0.10/leg slip:   ${df_d['pnl'].sum():+,.0f}  ({pct(df_d['pnl'].sum(), orig)})")

# Spot-check 5 random
log("\n" + "=" * 110)
log("SPOT-CHECK 5 RANDOM TRADES (Variant B — real exit, no slippage)")
log("=" * 110)
if not df_b.empty:
    random.seed(42)
    df_b_sorted = df_b.sort_values("trade_date").reset_index(drop=True)
    sample = random.sample(range(len(df_b_sorted)), min(5, len(df_b_sorted)))
    for idx in sample:
        r = df_b_sorted.iloc[idx]
        log(f"\nTrade {idx}: {r['trade_date']} ({r['dow']}), strikes ={r['sp']}/{r['sc']} (shorts), {r['lp']}/{r['lc']} (longs)")
        log(f"  Credit: ${r['credit']*100:.0f}  Exit: ${r['exit_val']*100:.0f}  Reason: {r['exit_reason']}  P&L: ${r['pnl']:+.0f}")

# Random partition
log("\n" + "=" * 110)
log("RANDOM PARTITION CROSS-VALIDATION (variant B)")
log("=" * 110)
if not df_b.empty and len(df_b) >= 10:
    random.seed(123)
    idxs = list(range(len(df_b)))
    random.shuffle(idxs)
    mid = len(idxs) // 2
    ha = df_b.iloc[idxs[:mid]]; hb = df_b.iloc[idxs[mid:]]
    log(f"  Half A ({len(ha)} trades):  Win={(ha['pnl']>0).mean()*100:.0f}%  Total=${ha['pnl'].sum():+,.0f}  Avg=${ha['pnl'].mean():+.0f}")
    log(f"  Half B ({len(hb)} trades):  Win={(hb['pnl']>0).mean()*100:.0f}%  Total=${hb['pnl'].sum():+,.0f}  Avg=${hb['pnl'].mean():+.0f}")

# P&L distribution
log("\n" + "=" * 110)
log("P&L DISTRIBUTION (variant B)")
log("=" * 110)
if not df_b.empty:
    bins = [(-9999, -300), (-300, -100), (-100, 0), (0, 100), (100, 300), (300, 600), (600, 9999)]
    for lo, hi in bins:
        n = ((df_b["pnl"] >= lo) & (df_b["pnl"] < hi)).sum()
        log(f"  ${lo:>+5} to ${hi:>+5}: {n:>3} ({n/len(df_b)*100:.1f}%)")

# Final summary
log("\n" + "=" * 110)
log("AVGO VERIFICATION SUMMARY")
log("=" * 110)
log(f"Claimed (Original):                ${orig:+,.0f}  ({(df_a['pnl']>0).mean()*100 if not df_a.empty else 0:.0f}% win)")
if not df_b.empty:
    log(f"With real-exit fix:                ${df_b['pnl'].sum():+,.0f}  ({(df_b['pnl']>0).mean()*100:.0f}% win)")
if not df_c.empty:
    log(f"With realistic slippage ($0.05):   ${df_c['pnl'].sum():+,.0f}  ({(df_c['pnl']>0).mean()*100:.0f}% win)")
if not df_d.empty:
    log(f"With conservative slip ($0.10):    ${df_d['pnl'].sum():+,.0f}  ({(df_d['pnl']>0).mean()*100:.0f}% win)")

log(f"\nPer-trade EV comparison (12 weeks → 1 year extrapolation):")
weeks = 12; annual_mult = 52/weeks
if not df_a.empty:
    log(f"  Original:       ${df_a['pnl'].mean():+.0f}/trade  →  ${df_a['pnl'].sum()*annual_mult:+,.0f}/year")
if not df_b.empty:
    log(f"  Real exit:      ${df_b['pnl'].mean():+.0f}/trade  →  ${df_b['pnl'].sum()*annual_mult:+,.0f}/year")
if not df_c.empty:
    log(f"  + slippage:     ${df_c['pnl'].mean():+.0f}/trade  →  ${df_c['pnl'].sum()*annual_mult:+,.0f}/year")
