"""JNJ Iron Condor backtest using 2.5 years of wide data.

JNJ has Friday-only weeklies (verified). Test multiple IC structures and
do proper train/test validation given the larger sample.
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

DATA = Path(__file__).parent / "data" / "jnj_wide"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 2.5  # JNJ strike spacing

def log(m): print(m, flush=True)


# ─── Load ──────────────────────────────────────────────────────────────────
log("Loading JNJ wide data (2024-2026)...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA.glob("jnj_cbbo_*.parquet")):
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
log(f"  Loaded ({time.time()-t0:.0f}s, {len(strikes_by_day)} trade dates)")

# Get all years in data
years = sorted({td.year for td in strikes_by_day.keys()})
log(f"  Years covered: {years}")

spot_hist = yf.Ticker("JNJ").history(start="2024-01-01", end="2026-06-01", auto_adjust=False)
spot_hist.index = pd.to_datetime(spot_hist.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot_hist.iterrows()}


def find_strike(avail, target, spacing=SPACING):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d_mult in range(1, 12):
        for sign in [+1, -1]:
            t = snapped + sign * d_mult * spacing
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate_legs(td, target_exp, legs, spot_exp_close, entry_ts, exit_ts,
                  stop_mult=2.0, pt_frac=0.5):
    leg_data = []; credit = 0
    for (strike, right, side) in legs:
        leg_df = expiry_index.get((td, target_exp, strike, right))
        if leg_df is None: return None
        e = at_or_after(leg_df, entry_ts)
        if e is None: return None
        if side == -1: credit += float(e["bid_px_00"])
        else: credit -= float(e["ask_px_00"])
        leg_data.append({"strike": strike, "right": right, "side": side, "df": leg_df})
    if credit < 0.05: return None  # JNJ has small credits, lower threshold

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    aligned = None
    for i, l in enumerate(leg_data):
        win = l["df"].loc[(l["df"].index >= entry_ts) & (l["df"].index <= exit_ts)]
        if win.empty: return None
        ren = win.rename(columns={"bid_px_00": f"b_{i}", "ask_px_00": f"a_{i}"})
        aligned = ren if aligned is None else aligned.join(ren, how="inner")
    if aligned is None or aligned.empty: return None

    debit = pd.Series(0.0, index=aligned.index)
    for i, l in enumerate(leg_data):
        if l["side"] == -1: debit += aligned[f"a_{i}"]
        else: debit -= aligned[f"b_{i}"]

    sl = aligned[debit >= stop_debit]; pt = aligned[debit <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = debit.loc[sl_ts]; exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = debit.loc[pt_ts]; exit_reason = "profit_target"
    else:
        v = 0
        for l in leg_data:
            if l["right"] == "P": intrinsic = max(0, l["strike"] - spot_exp_close)
            else: intrinsic = max(0, spot_exp_close - l["strike"])
            v += (1 if l["side"] == -1 else -1) * intrinsic
        exit_val = v; exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {"credit": credit, "credit_dollars": credit*100,
            "exit_reason": exit_reason, "pnl": pnl}


def build_ic(avail, spot, otm_put, otm_call, wing_pct):
    sp = find_strike(avail, spot * (1 - otm_put))
    sc = find_strike(avail, spot * (1 + otm_call))
    if sp is None or sc is None: return None
    wing = spot * wing_pct
    lp = find_strike(avail, sp - wing); lc = find_strike(avail, sc + wing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (sp, "P", -1), (sc, "C", -1), (lc, "C", +1)]


def build_iron_fly(avail, spot, wing_pct):
    atm = find_strike(avail, spot)
    if atm is None: return None
    wing = spot * wing_pct
    lp = find_strike(avail, atm - wing); lc = find_strike(avail, atm + wing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (atm, "P", -1), (atm, "C", -1), (lc, "C", +1)]


def run_strategy(build_fn, **kwargs):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        # Find same-day expiry (Friday only)
        available_exps = sorted(strikes_by_day[td].keys())
        target_exp = None
        for exp in available_exps:
            if exp == td and exp.weekday() == 4:  # same-day Friday expiry
                target_exp = exp
                break
        if target_exp is None: continue
        if td not in spot_by_date: continue

        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)

        avail = strikes_by_day[td][target_exp]
        legs = build_fn(avail, spot_entry, **kwargs)
        if legs is None: continue

        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=9, minute=45).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=15, minute=45).astimezone(UTC))
        r = simulate_legs(td, target_exp, legs, spot_c, entry_ts, exit_ts)
        if r:
            r["trade_date"] = td; r["dow"] = td.strftime("%a"); r["year"] = td.year
            trades.append(r)
    return pd.DataFrame(trades)


def summarize(df):
    if df.empty: return None
    n = len(df); wins = (df["pnl"] > 0).sum()
    df_sorted = df.sort_values("trade_date").reset_index(drop=True) if "trade_date" in df.columns else df.reset_index(drop=True)
    df_sorted["cum"] = df_sorted["pnl"].cumsum()
    df_sorted["peak"] = df_sorted["cum"].cummax()
    df_sorted["dd"] = df_sorted["cum"] - df_sorted["peak"]
    return {
        "n": n, "win_rate": wins/n*100,
        "avg_credit": df["credit_dollars"].mean(),
        "total_pnl": df["pnl"].sum(), "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(), "worst": df["pnl"].min(),
        "max_dd": df_sorted["dd"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
        "pts": (df["exit_reason"] == "profit_target").sum(),
    }


# ─── Run strategy sweep ────────────────────────────────────────────────────
log("\n" + "=" * 100)
log("JNJ STRATEGY SWEEP — 2.5 years (Friday-only weeklies)")
log("=" * 100)

results = []

# IC at various OTM/wing combos
for otm in [0.005, 0.01, 0.015, 0.02, 0.03]:
    for wp in [0.04, 0.06, 0.08]:
        df = run_strategy(build_ic, otm_put=otm, otm_call=otm, wing_pct=wp)
        s = summarize(df)
        if s and s["n"] >= 30:
            results.append({"strategy": f"IC_{otm*100:.1f}%_{wp*100:.0f}%w", **s})

# Iron Fly
for wp in [0.03, 0.04, 0.06, 0.08]:
    df = run_strategy(build_iron_fly, wing_pct=wp)
    s = summarize(df)
    if s and s["n"] >= 30:
        results.append({"strategy": f"IFLY_{wp*100:.0f}%w", **s})

# Asymmetric
for op in [0.005, 0.01]:
    for oc in [0.01, 0.02, 0.03]:
        if op == oc: continue
        df = run_strategy(build_ic, otm_put=op, otm_call=oc, wing_pct=0.06)
        s = summarize(df)
        if s and s["n"] >= 30:
            results.append({"strategy": f"IC_P{op*100:.1f}%_C{oc*100:.1f}%_6%w", **s})

res = pd.DataFrame(results).sort_values("total_pnl", ascending=False)
log(f"\n{'Strategy':<28}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'Total':<11}{'Avg':<9}{'Best':<8}{'Worst':<9}{'MaxDD':<9}")
log("-" * 100)
for _, r in res.head(20).iterrows():
    log(f"{r['strategy']:<28}{int(r['n']):<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+8.0f}${r['best']:<+7.0f}${r['worst']:<+8.0f}${r['max_dd']:<+8.0f}")

# Save top variant for deeper analysis
if len(res) > 0:
    top = res.iloc[0]
    log(f"\n⭐ TOP STRATEGY: {top['strategy']}")

# ─── Deep dive on TOP strategy ─────────────────────────────────────────────
log("\n" + "=" * 100)
log("DEEP DIVE: Top strategy by year and bootstrap")
log("=" * 100)
# Parse top strategy params
top_name = res.iloc[0]["strategy"]
# Re-run top strategy and get full trade dataframe
if "IC_P" in top_name and "_C" in top_name:
    # asym
    m = re.match(r"IC_P([\d.]+)%_C([\d.]+)%_(\d+)%w", top_name)
    op = float(m.group(1)) / 100; oc = float(m.group(2)) / 100; wp = float(m.group(3)) / 100
    top_df = run_strategy(build_ic, otm_put=op, otm_call=oc, wing_pct=wp)
elif "IC_" in top_name:
    m = re.match(r"IC_([\d.]+)%_(\d+)%w", top_name)
    otm = float(m.group(1)) / 100; wp = float(m.group(2)) / 100
    top_df = run_strategy(build_ic, otm_put=otm, otm_call=otm, wing_pct=wp)
elif "IFLY" in top_name:
    m = re.match(r"IFLY_(\d+)%w", top_name)
    wp = float(m.group(1)) / 100
    top_df = run_strategy(build_iron_fly, wing_pct=wp)

# Per-year results
log("\nPer-year performance:")
log(f"  {'Year':<8}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Best':<9}{'Worst':<9}")
log("  " + "-" * 65)
for year in sorted(top_df["year"].unique()):
    sub = top_df[top_df["year"] == year]
    n = len(sub); wins = (sub["pnl"] > 0).sum()
    log(f"  {year:<8}{n:<5}{wins/n*100:<6.0f}%${sub['pnl'].sum():<+10,.0f}${sub['pnl'].mean():<+8.0f}${sub['pnl'].max():<+8.0f}${sub['pnl'].min():<+8.0f}")

# Bootstrap CI
log("\n\nBOOTSTRAP CI (1000 resamples):")
random.seed(42)
pnls = top_df["pnl"].values
n_trades = len(pnls)
win_rates = []; avg_pnls = []; totals = []
for _ in range(1000):
    sample = np.random.choice(pnls, size=n_trades, replace=True)
    win_rates.append((sample > 0).mean() * 100)
    avg_pnls.append(sample.mean())
    totals.append(sample.sum())
win_rates = np.array(win_rates); avg_pnls = np.array(avg_pnls); totals = np.array(totals)
log(f"  Win rate:  {np.percentile(win_rates, 2.5):.1f}% — {np.percentile(win_rates, 97.5):.1f}%  (95% CI)")
log(f"  Avg/trade: ${np.percentile(avg_pnls, 2.5):+.0f} — ${np.percentile(avg_pnls, 97.5):+.0f}")
log(f"  Total P&L: ${np.percentile(totals, 2.5):+,.0f} — ${np.percentile(totals, 97.5):+,.0f}")
log(f"  Probability total > 0: {(totals > 0).mean()*100:.1f}%")

# Train/test split (2024 train, 2025-2026 test)
log("\n\nTRAIN/TEST SPLIT:")
train = top_df[top_df["year"] == 2024]
test = top_df[top_df["year"] >= 2025]
log(f"  TRAIN (2024, {len(train)} trades):")
ts = summarize(train)
if ts:
    log(f"    Win={ts['win_rate']:.0f}%, Total=${ts['total_pnl']:+,.0f}, Avg=${ts['avg_pnl']:+.0f}, Worst=${ts['worst']:+.0f}")
log(f"  TEST  (2025-2026, {len(test)} trades):")
ts = summarize(test)
if ts:
    log(f"    Win={ts['win_rate']:.0f}%, Total=${ts['total_pnl']:+,.0f}, Avg=${ts['avg_pnl']:+.0f}, Worst=${ts['worst']:+.0f}")

# Worst single trades (look for known events)
log("\n\nTop 10 WORST trades (might align with earnings/news):")
worst = top_df.nsmallest(10, "pnl")[["trade_date", "credit_dollars", "pnl", "exit_reason"]]
for _, r in worst.iterrows():
    log(f"  {r['trade_date']}  credit ${r['credit_dollars']:.0f}  P&L ${r['pnl']:+.0f}  exit={r['exit_reason']}")

top_df.to_csv("/tmp/jnj_top_strategy_trades.csv", index=False)
res.to_csv("/tmp/jnj_strategy_summary.csv", index=False)
log("\nResults saved.")
