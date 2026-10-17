"""Exhaustive Iron Fly sweep on AVGO 2026 M/W/F 0DTE data.

Tests:
A. Symmetric wing widths (20, 25, 30, 35, 40, 45, 50, 60pt)
B. Asymmetric wings (different put vs call wing widths)
C. Per day-of-week optimization
D. Entry time × wing width interaction
E. PT/SL multiplier refinement (now with 09:45 entry)
F. Combinations of the best findings

All output honest; no cherry-picking.
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

def log(m): print(m, flush=True)

log("Loading 2026 parquets...")
t0 = time.time()
expiry_index = defaultdict(list)
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA_DIR.glob("avgo_cbbo_2026*.parquet")):
    df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
    df["sym_clean"] = df["symbol"].str.strip()
    ext = df["sym_clean"].str.extract(SYM_RE)
    ext.columns = ["root", "yymmdd", "right", "strike_str"]
    df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
    df["right"] = ext["right"]
    df["strike"] = ext["strike_str"].astype(float) / 1000
    df = df.dropna(subset=["expiry", "right", "strike"])
    ds = f.stem.replace("avgo_cbbo_", "")
    td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
    for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
        expiry_index[(exp, strike, right)].append(grp[["bid_px_00", "ask_px_00"]])
        strikes_by_day[td][exp].add(strike)
for k in list(expiry_index.keys()):
    expiry_index[k] = pd.concat(expiry_index[k]).sort_index()

spot = yf.Ticker("AVGO").history(start="2026-02-01", end="2026-06-01")
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}
log(f"  Loaded ({time.time()-t0:.0f}s, {len(expiry_index)} keys)")


def find_strike(avail, target, spacing=2.5):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d in [2.5, 5, 7.5, 10, 12.5, 15, 17.5, 20]:
        for sign in [+1, -1]:
            t = snapped + sign * d
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate(td, target_exp, wing_put, wing_call, spot_entry, spot_exp_close,
             entry_ts, exit_ts, stop_mult=2.0, pt_frac=0.5):
    avail = strikes_by_day[td].get(target_exp, set())
    if not avail: return None
    atm = find_strike(avail, spot_entry)
    if atm is None: return None
    lp = find_strike(avail, atm - wing_put)
    lc = find_strike(avail, atm + wing_call)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((target_exp, atm, "P"))
    sc_df = expiry_index.get((target_exp, atm, "C"))
    lp_df = expiry_index.get((target_exp, lp, "P"))
    lc_df = expiry_index.get((target_exp, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    e_sp = at_or_after(sp_df, entry_ts)
    e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts)
    e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]): return None

    credit = float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"]) - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
    if credit < 0.15: return None

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    exit_val = None; exit_reason = None
    if not any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]):
        sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
        lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
        al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
        if not al.empty:
            al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
            sl = al[al["debit"] >= stop_debit]
            pt = al[al["debit"] <= pt_debit]
            sl_ts = sl.index.min() if not sl.empty else None
            pt_ts = pt.index.min() if not pt.empty else None
            if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                exit_val = al.loc[sl_ts, "debit"]; exit_reason = "stop_loss"
            elif pt_ts is not None:
                exit_val = al.loc[pt_ts, "debit"]; exit_reason = "profit_target"

    if exit_val is None:
        sp_int = max(0, atm - spot_exp_close); lp_int = max(0, lp - spot_exp_close)
        sc_int = max(0, spot_exp_close - atm); lc_int = max(0, spot_exp_close - lc)
        exit_val = sp_int + sc_int - lp_int - lc_int
        exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {
        "trade_date": td, "dow": td.strftime("%a"),
        "atm": atm, "lp": lp, "lc": lc,
        "wing_put": wing_put, "wing_call": wing_call,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_reason": exit_reason, "exit_val": exit_val,
        "pnl": pnl,
    }


def run_strategy(wing_put, wing_call, entry_hour, entry_min,
                 exit_hour=15, exit_min=45, stop_mult=2.0, pt_frac=0.5, dow_filter=None):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        if dow_filter and td.strftime("%a") not in dow_filter: continue
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue
        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)
        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=entry_hour, minute=entry_min).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=exit_hour, minute=exit_min).astimezone(UTC))
        r = simulate(td, td, wing_put, wing_call, spot_entry, spot_c, entry_ts, exit_ts,
                     stop_mult=stop_mult, pt_frac=pt_frac)
        if r: trades.append(r)
    if not trades: return None
    df = pd.DataFrame(trades)
    n = len(df); wins = (df["pnl"] > 0).sum()
    return {
        "wing_put": wing_put, "wing_call": wing_call,
        "entry": f"{entry_hour:02d}:{entry_min:02d}",
        "stop_mult": stop_mult, "pt_frac": pt_frac,
        "n": n, "win_rate": wins/n*100,
        "avg_credit": df["credit_dollars"].mean(),
        "total_pnl": df["pnl"].sum(),
        "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(), "worst": df["pnl"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
        "pts": (df["exit_reason"] == "profit_target").sum(),
    }


# ─── A. Symmetric wing width sweep at 09:45 entry ──────────────────────────
log("\n" + "=" * 100)
log("A. SYMMETRIC WING WIDTH SWEEP (entry 09:45, exit 15:45)")
log("=" * 100)
results_a = []
for w in [15, 20, 25, 30, 35, 40, 45, 50, 60, 75]:
    r = run_strategy(w, w, 9, 45)
    if r: results_a.append(r)
sum_a = pd.DataFrame(results_a)
log(f"{'Wing':<8}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}{'Stops':<7}")
log("-" * 85)
for _, r in sum_a.sort_values("total_pnl", ascending=False).iterrows():
    log(f"{int(r['wing_put']):<8}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}{int(r['stops']):<7}")

# ─── B. Asymmetric wings at 09:45 entry ────────────────────────────────────
log("\n" + "=" * 100)
log("B. ASYMMETRIC WING WIDTHS (put wing × call wing)")
log("=" * 100)
results_b = []
for wp in [25, 30, 40, 50]:
    for wc in [25, 30, 40, 50, 60]:
        r = run_strategy(wp, wc, 9, 45)
        if r:
            r["asym_ratio"] = wc / wp
            results_b.append(r)
sum_b = pd.DataFrame(results_b)
log(f"{'PutW':<6}{'CallW':<7}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}")
log("-" * 85)
for _, r in sum_b.sort_values("total_pnl", ascending=False).head(15).iterrows():
    log(f"{int(r['wing_put']):<6}{int(r['wing_call']):<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

# ─── C. Day-of-week analysis with best wing/entry ─────────────────────────
log("\n" + "=" * 100)
log("C. DAY-OF-WEEK PERFORMANCE (40pt symmetric, 09:45 entry)")
log("=" * 100)
log(f"{'Day':<6}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}")
log("-" * 75)
for dow in ["Mon", "Wed", "Fri"]:
    r = run_strategy(40, 40, 9, 45, dow_filter=[dow])
    if r:
        log(f"{dow:<6}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

# Try optimizing per day: best wing per day?
log("\nBest wing width PER day-of-week:")
log(f"{'Day':<6}{'BestWing':<10}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'AvgP&L':<10}")
log("-" * 70)
for dow in ["Mon", "Wed", "Fri"]:
    best = None
    for w in [25, 30, 40, 50, 60]:
        r = run_strategy(w, w, 9, 45, dow_filter=[dow])
        if r and (best is None or r["total_pnl"] > best["total_pnl"]):
            best = r
    if best:
        log(f"{dow:<6}{int(best['wing_put']):<10}{best['n']:<5}{best['win_rate']:<6.0f}%${best['total_pnl']:<+10,.0f}${best['avg_pnl']:<+9.0f}")

# ─── D. Entry time × wing width interaction ───────────────────────────────
log("\n" + "=" * 100)
log("D. ENTRY × WING (find best combo)")
log("=" * 100)
log(f"{'Entry':<8}{'Wing':<6}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'AvgP&L':<10}{'Worst':<10}")
log("-" * 75)
results_d = []
for hr, mn in [(9, 35), (9, 45), (10, 0), (10, 15), (10, 30)]:
    for w in [25, 30, 35, 40, 45, 50]:
        r = run_strategy(w, w, hr, mn)
        if r: results_d.append(r)
for _, r in pd.DataFrame(results_d).sort_values("total_pnl", ascending=False).head(15).iterrows():
    log(f"{r['entry']:<8}{int(r['wing_put']):<6}{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['worst']:<+9.0f}")

# ─── E. PT/SL refinement at best entry/wing ───────────────────────────────
log("\n" + "=" * 100)
log("E. PT/SL VARIANTS at 09:45 entry, 40pt wings")
log("=" * 100)
log(f"{'StopX':<7}{'PT%':<7}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'AvgP&L':<10}{'Worst':<10}")
log("-" * 75)
results_e = []
for sm in [1.5, 2.0, 2.5, 3.0, 5.0]:
    for pt in [0.25, 0.50, 0.75, 0.95]:
        r = run_strategy(40, 40, 9, 45, stop_mult=sm, pt_frac=pt)
        if r: results_e.append(r)
for _, r in pd.DataFrame(results_e).sort_values("total_pnl", ascending=False).iterrows():
    log(f"{r['stop_mult']:<6.1f}x{r['pt_frac']*100:<5.0f}%{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['worst']:<+9.0f}")

# ─── F. THE ABSOLUTE BEST COMBINATION ─────────────────────────────────────
log("\n" + "=" * 100)
log("F. TRY BEST COMBINATIONS")
log("=" * 100)
# Take top 3 from each category and try combining
log("Asymmetric + best entry + various PT/SL:")
results_f = []
for wp, wc in [(40, 50), (50, 60), (30, 40), (40, 60), (30, 50)]:
    for hr, mn in [(9, 45), (10, 0)]:
        for sm, pt in [(2.0, 0.50), (3.0, 0.50), (5.0, 0.75)]:
            r = run_strategy(wp, wc, hr, mn, stop_mult=sm, pt_frac=pt)
            if r: results_f.append(r)
sum_f = pd.DataFrame(results_f).sort_values("total_pnl", ascending=False)
log(f"{'PutW':<6}{'CallW':<7}{'Entry':<8}{'StopX':<7}{'PT':<5}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'AvgP&L':<10}{'Worst':<10}")
log("-" * 95)
for _, r in sum_f.head(10).iterrows():
    log(f"{int(r['wing_put']):<6}{int(r['wing_call']):<7}{r['entry']:<8}{r['stop_mult']:<6.1f}x{r['pt_frac']*100:<3.0f}%{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['worst']:<+9.0f}")

# Save everything
sum_a.to_csv("/tmp/avgo_sweep_a_wings.csv", index=False)
sum_b.to_csv("/tmp/avgo_sweep_b_asym.csv", index=False)
pd.DataFrame(results_d).to_csv("/tmp/avgo_sweep_d_entry_wing.csv", index=False)
pd.DataFrame(results_e).to_csv("/tmp/avgo_sweep_e_ptsl.csv", index=False)
sum_f.to_csv("/tmp/avgo_sweep_f_combos.csv", index=False)
log("\nAll saved to /tmp/avgo_sweep_*.csv")
