"""IWM Iron Condor backtest with DAILY expiries (2.5 years).

IWM has M-T-W-R-F daily weekly expiries → 5 trades/week potential.
600 days × up to 5 trades/day = MASSIVE sample size.
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
SPACING = 1.0  # IWM $1 strike spacing

def log(m): print(m, flush=True)


log("Loading IWM wide data (2024-2026)...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
log(f"  Files: {len(files)}")
for i, f in enumerate(files):
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
    if (i + 1) % 100 == 0:
        log(f"  loaded {i+1}/{len(files)} ({time.time()-t0:.0f}s)")
log(f"  Done ({time.time()-t0:.0f}s, {len(strikes_by_day)} dates)")

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
    if credit < 0.05: return None

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
        # Find same-day expiry (any day-of-week)
        if td not in strikes_by_day[td]: continue  # no 0DTE
        if td not in spot_by_date: continue

        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)

        avail = strikes_by_day[td][td]
        legs = build_fn(avail, spot_entry, **kwargs)
        if legs is None: continue

        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=9, minute=45).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=15, minute=45).astimezone(UTC))
        r = simulate_legs(td, td, legs, spot_c, entry_ts, exit_ts)
        if r:
            r["trade_date"] = td; r["dow"] = td.strftime("%a"); r["year"] = td.year
            trades.append(r)
    return pd.DataFrame(trades)


def summarize(df):
    if df.empty: return None
    n = len(df); wins = (df["pnl"] > 0).sum()
    sorted_df = df.sort_values("trade_date").reset_index(drop=True) if "trade_date" in df.columns else df.reset_index(drop=True)
    sorted_df["cum"] = sorted_df["pnl"].cumsum()
    sorted_df["peak"] = sorted_df["cum"].cummax()
    sorted_df["dd"] = sorted_df["cum"] - sorted_df["peak"]
    return {
        "n": n, "win_rate": wins/n*100,
        "avg_credit": df["credit_dollars"].mean(),
        "total_pnl": df["pnl"].sum(), "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(), "worst": df["pnl"].min(),
        "max_dd": sorted_df["dd"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
        "pts": (df["exit_reason"] == "profit_target").sum(),
    }


log("\n" + "=" * 110)
log("IWM STRATEGY SWEEP — 2.5 years × DAILY 0DTE")
log("=" * 110)

results = []

# IC at various OTM/wing combos
for otm in [0.005, 0.01, 0.015, 0.02, 0.03]:
    for wp in [0.03, 0.04, 0.06, 0.08]:
        df = run_strategy(build_ic, otm_put=otm, otm_call=otm, wing_pct=wp)
        s = summarize(df)
        if s and s["n"] >= 50:
            results.append({"strategy": f"IC_{otm*100:.1f}%_{wp*100:.0f}%w", **s})

# Iron Fly
for wp in [0.02, 0.03, 0.04, 0.06, 0.08]:
    df = run_strategy(build_iron_fly, wing_pct=wp)
    s = summarize(df)
    if s and s["n"] >= 50:
        results.append({"strategy": f"IFLY_{wp*100:.0f}%w", **s})

# Asymmetric
for op in [0.005, 0.01]:
    for oc in [0.01, 0.02, 0.03]:
        if op == oc: continue
        df = run_strategy(build_ic, otm_put=op, otm_call=oc, wing_pct=0.06)
        s = summarize(df)
        if s and s["n"] >= 50:
            results.append({"strategy": f"IC_P{op*100:.1f}%_C{oc*100:.1f}%_6%w", **s})

res = pd.DataFrame(results).sort_values("total_pnl", ascending=False)
log(f"\n{'Strategy':<28}{'N':<6}{'Win%':<7}{'AvgCr':<8}{'Total':<11}{'Avg':<9}{'Best':<8}{'Worst':<9}{'MaxDD':<9}")
log("-" * 105)
for _, r in res.head(20).iterrows():
    log(f"{r['strategy']:<28}{int(r['n']):<6}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+8.0f}${r['best']:<+7.0f}${r['worst']:<+8.0f}${r['max_dd']:<+8.0f}")

# Deep dive on TOP
if len(res) > 0:
    top = res.iloc[0]
    log(f"\n⭐ TOP STRATEGY: {top['strategy']}")
    log(f"  N={int(top['n'])} trades over 2.5 years")
    log(f"  Per week avg: {top['n']/130:.1f} trades  (130 weeks)")
    log(f"  Annual income estimate: ${top['total_pnl']/2.5:+,.0f}")

    # Re-run top
    top_name = top["strategy"]
    if "IC_P" in top_name and "_C" in top_name:
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

    # Per-year
    log(f"\nPER-YEAR for top strategy:")
    log(f"  {'Year':<8}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Worst':<9}{'MaxDD':<9}")
    log("  " + "-" * 65)
    for year in sorted(top_df["year"].unique()):
        sub = top_df[top_df["year"] == year]
        s = summarize(sub)
        log(f"  {year:<8}{s['n']:<5}{s['win_rate']:<6.0f}%${s['total_pnl']:<+10,.0f}${s['avg_pnl']:<+8.0f}${s['worst']:<+8.0f}${s['max_dd']:<+8.0f}")

    # Per day-of-week
    log(f"\nPER DAY-OF-WEEK for top strategy:")
    log(f"  {'Day':<6}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Best':<8}{'Worst':<9}")
    log("  " + "-" * 60)
    for dow in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        sub = top_df[top_df["dow"] == dow]
        if sub.empty: continue
        s = summarize(sub)
        log(f"  {dow:<6}{s['n']:<5}{s['win_rate']:<6.0f}%${s['total_pnl']:<+10,.0f}${s['avg_pnl']:<+8.0f}${s['best']:<+7.0f}${s['worst']:<+8.0f}")

    # Bootstrap
    log(f"\nBOOTSTRAP CI (1000 resamples):")
    random.seed(42)
    pnls = top_df["pnl"].values
    n_trades = len(pnls)
    wrs = []; avgs = []; totals = []
    for _ in range(1000):
        sample = np.random.choice(pnls, size=n_trades, replace=True)
        wrs.append((sample > 0).mean() * 100); avgs.append(sample.mean()); totals.append(sample.sum())
    wrs = np.array(wrs); avgs = np.array(avgs); totals = np.array(totals)
    log(f"  Win rate:  {np.percentile(wrs, 2.5):.1f}% — {np.percentile(wrs, 97.5):.1f}%")
    log(f"  Avg/trade: ${np.percentile(avgs, 2.5):+.0f} — ${np.percentile(avgs, 97.5):+.0f}")
    log(f"  Total P&L: ${np.percentile(totals, 2.5):+,.0f} — ${np.percentile(totals, 97.5):+,.0f}")
    log(f"  Probability total > 0: {(totals > 0).mean()*100:.1f}%")

    # Train/test split
    log(f"\nTRAIN/TEST SPLIT:")
    train = top_df[top_df["year"] == 2024]
    test = top_df[top_df["year"] >= 2025]
    if len(train) > 10 and len(test) > 10:
        log(f"  TRAIN (2024, {len(train)} trades):")
        ts = summarize(train)
        log(f"    Win={ts['win_rate']:.0f}%, Total=${ts['total_pnl']:+,.0f}, Avg=${ts['avg_pnl']:+.0f}, Worst=${ts['worst']:+.0f}")
        log(f"  TEST (2025-2026, {len(test)} trades):")
        ts = summarize(test)
        log(f"    Win={ts['win_rate']:.0f}%, Total=${ts['total_pnl']:+,.0f}, Avg=${ts['avg_pnl']:+.0f}, Worst=${ts['worst']:+.0f}")

    top_df.to_csv("/tmp/iwm_top_trades.csv", index=False)

res.to_csv("/tmp/iwm_strategy_summary.csv", index=False)
log("\nResults saved.")
