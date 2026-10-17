"""Re-run NVDA strategy sweep with WIDE strike pull (±8% OTM)."""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data" / "nvda_wide"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 5.0  # NVDA actual strike spacing at OTM range

def log(m): print(m, flush=True)

log("Loading NVDA wide data...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA.glob("nvda_cbbo_*.parquet")):
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

# Show actual strike availability
log("\nStrike availability examples (same-day expiry):")
for d in [date(2026, 3, 2), date(2026, 4, 6), date(2026, 5, 18)]:
    if d in strikes_by_day:
        if d in strikes_by_day[d]:
            strikes = sorted(strikes_by_day[d][d])
            log(f"  {d}: {len(strikes)} strikes  range ${strikes[0]:.0f}-${strikes[-1]:.0f}")

spot_hist = yf.Ticker("NVDA").history(start="2026-01-01", end="2026-06-01")
spot_hist.index = pd.to_datetime(spot_hist.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot_hist.iterrows()}


def find_strike(avail, target, spacing=SPACING):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d_mult in range(1, 10):
        for sign in [+1, -1]:
            t = snapped + sign * d_mult * spacing
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate_legs(td, target_exp, legs, spot_exp_close, entry_ts, exit_ts):
    leg_data = []; credit = 0
    for (strike, right, side) in legs:
        leg_df = expiry_index.get((td, target_exp, strike, right))
        if leg_df is None: return None
        e = at_or_after(leg_df, entry_ts)
        if e is None: return None
        if side == -1: credit += float(e["bid_px_00"])
        else: credit -= float(e["ask_px_00"])
        leg_data.append({"strike": strike, "right": right, "side": side, "df": leg_df})
    if credit < 0.10: return None
    stop_debit = credit * 2.0
    pt_debit = credit * 0.5
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
    return {"credit": credit, "exit_reason": exit_reason, "pnl": pnl}


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


def run(build_fn, **kwargs):
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue
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
        if r: trades.append(r)
    return pd.DataFrame(trades)


def summarize(df):
    if df.empty: return None
    n = len(df); wins = (df["pnl"] > 0).sum()
    return {"n": n, "win_rate": wins/n*100, "total_pnl": df["pnl"].sum(),
            "avg_pnl": df["pnl"].mean(), "best": df["pnl"].max(), "worst": df["pnl"].min()}


log("\n" + "=" * 100)
log("NVDA WIDE — Strategy sweep with $5 effective strike spacing")
log("=" * 100)

results = []

# IC variants
for otm in [0.005, 0.01, 0.015, 0.02, 0.03]:
    for wp in [0.04, 0.06, 0.08, 0.10]:
        df = run(build_ic, otm_put=otm, otm_call=otm, wing_pct=wp)
        s = summarize(df)
        if s and s["n"] >= 15:
            results.append({"strategy": f"IC_{otm*100:.1f}%_{wp*100:.0f}%w", **s})

# Asymmetric IC
for op in [0.005, 0.01, 0.02]:
    for oc in [0.01, 0.02, 0.03, 0.04]:
        if op == oc: continue
        df = run(build_ic, otm_put=op, otm_call=oc, wing_pct=0.06)
        s = summarize(df)
        if s and s["n"] >= 15:
            results.append({"strategy": f"IC_P{op*100:.1f}%_C{oc*100:.1f}%_6%w", **s})

# Iron Fly
for wp in [0.04, 0.06, 0.08, 0.10]:
    df = run(build_iron_fly, wing_pct=wp)
    s = summarize(df)
    if s and s["n"] >= 15:
        results.append({"strategy": f"IFLY_{wp*100:.0f}%w", **s})

res_df = pd.DataFrame(results).sort_values("total_pnl", ascending=False)
log(f"\n{'Strategy':<28}{'N':<5}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Best':<9}{'Worst':<9}")
log("-" * 80)
for _, r in res_df.head(20).iterrows():
    log(f"{r['strategy']:<28}{int(r['n']):<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+8.0f}${r['best']:<+8.0f}${r['worst']:<+8.0f}")

# Compare to previous narrow result
log(f"\n--- COMPARISON ---")
log(f"Previous NVDA (narrow ±5% pull) best:")
log(f"  IC_1.0%_6%w: N=43, Win=77%, Total=$+1,328, Avg=$+31, Worst=$-232")
log(f"NVDA wide (±8% pull) best:")
top = res_df.iloc[0] if len(res_df) > 0 else None
if top is not None:
    log(f"  {top['strategy']}: N={int(top['n'])}, Win={top['win_rate']:.0f}%, Total=${top['total_pnl']:+,.0f}, Avg=${top['avg_pnl']:+.0f}, Worst=${top['worst']:+.0f}")
