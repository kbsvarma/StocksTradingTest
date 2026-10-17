"""Test BPS, BCS, slight-OTM Iron Fly, Jade Lizard etc. on AVGO 2026.

All at 09:45 entry, 15:45 forced exit, 50% PT, 2.0x SL, M/W/F 0DTE.
Baseline = Iron Fly 40pt symmetric ATM = $5,778 / 73% win.
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
log(f"Loaded ({time.time()-t0:.0f}s)")


def find_strike(avail, target, spacing=2.5):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d in [2.5, 5, 7.5, 10, 12.5, 15]:
        for sign in [+1, -1]:
            t = snapped + sign * d
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def get_leg(exp, strike, right):
    return expiry_index.get((exp, strike, right))


def simulate_legs(td, target_exp, legs, spot_exp_close, entry_ts, exit_ts,
                  stop_mult=2.0, pt_frac=0.5):
    """legs: list of (strike, right, side) where side=+1 long, -1 short."""
    quotes_at_entry = []
    leg_data = []
    entry_credit = 0
    for (strike, right, side) in legs:
        leg_df = get_leg(target_exp, strike, right)
        if leg_df is None: return None
        e = at_or_after(leg_df, entry_ts)
        if e is None: return None
        if side == -1:
            entry_credit += float(e["bid_px_00"])
        else:
            entry_credit -= float(e["ask_px_00"])
        leg_data.append({"strike": strike, "right": right, "side": side, "df": leg_df})

    if entry_credit < 0.10: return None

    # Max width across all legs (for max loss calc)
    strikes = [l["strike"] for l in leg_data]

    stop_debit = entry_credit * stop_mult
    pt_debit = entry_credit * (1 - pt_frac)

    # Walk debit-to-close minute by minute
    aligned = None
    for i, l in enumerate(leg_data):
        win = l["df"].loc[(l["df"].index >= entry_ts) & (l["df"].index <= exit_ts)]
        if win.empty: return None
        ren = win.rename(columns={"bid_px_00": f"b_{i}", "ask_px_00": f"a_{i}"})
        aligned = ren if aligned is None else aligned.join(ren, how="inner")
    if aligned is None or aligned.empty: return None

    # Compute debit-to-close
    debit = pd.Series(0.0, index=aligned.index)
    for i, l in enumerate(leg_data):
        if l["side"] == -1:  # short → pay ask
            debit += aligned[f"a_{i}"]
        else:  # long → receive bid
            debit -= aligned[f"b_{i}"]

    exit_val = None; exit_reason = None
    sl = aligned[debit >= stop_debit]
    pt = aligned[debit <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = debit.loc[sl_ts]; exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = debit.loc[pt_ts]; exit_reason = "profit_target"

    if exit_val is None:
        # Settle at expiry
        v = 0
        for l in leg_data:
            if l["right"] == "P":
                intrinsic = max(0, l["strike"] - spot_exp_close)
            else:
                intrinsic = max(0, spot_exp_close - l["strike"])
            v += (1 if l["side"] == -1 else -1) * intrinsic
        exit_val = v
        exit_reason = "expiry"

    pnl = (entry_credit - exit_val) * 100
    return {"trade_date": td, "dow": td.strftime("%a"),
            "credit": entry_credit, "credit_dollars": entry_credit * 100,
            "exit_reason": exit_reason, "pnl": pnl}


def run(strategy_name, build_legs, **kwargs):
    """build_legs(avail, spot_entry) -> list of (strike, right, side) or None"""
    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue
        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)
        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=9, minute=45).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=15, minute=45).astimezone(UTC))

        avail = strikes_by_day[td][td]
        legs = build_legs(avail, spot_entry, **kwargs)
        if legs is None: continue
        r = simulate_legs(td, td, legs, spot_c, entry_ts, exit_ts)
        if r: trades.append(r)
    if not trades: return None
    df = pd.DataFrame(trades)
    return {
        "strategy": strategy_name,
        "n": len(df),
        "win_rate": (df["pnl"] > 0).mean() * 100,
        "avg_credit": df["credit_dollars"].mean(),
        "total_pnl": df["pnl"].sum(),
        "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(),
        "worst": df["pnl"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
        "pts": (df["exit_reason"] == "profit_target").sum(),
    }


# ─── Strategy builders ─────────────────────────────────────────────────────
def bps(avail, spot, otm_pct, wing):
    sp = find_strike(avail, spot * (1 - otm_pct))
    if sp is None: return None
    lp = find_strike(avail, sp - wing)
    if lp is None: return None
    return [(sp, "P", -1), (lp, "P", +1)]


def bcs(avail, spot, otm_pct, wing):
    sc = find_strike(avail, spot * (1 + otm_pct))
    if sc is None: return None
    lc = find_strike(avail, sc + wing)
    if lc is None: return None
    return [(sc, "C", -1), (lc, "C", +1)]


def iron_fly(avail, spot, wing, offset_pct=0):
    """Iron Fly with optional offset from ATM."""
    atm = find_strike(avail, spot * (1 + offset_pct))  # offset can be slightly OTM
    if atm is None: return None
    lp = find_strike(avail, atm - wing)
    lc = find_strike(avail, atm + wing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (atm, "P", -1), (atm, "C", -1), (lc, "C", +1)]


def iron_condor(avail, spot, otm_put, otm_call, wing):
    sp = find_strike(avail, spot * (1 - otm_put))
    sc = find_strike(avail, spot * (1 + otm_call))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing)
    lc = find_strike(avail, sc + wing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (sp, "P", -1), (sc, "C", -1), (lc, "C", +1)]


def jade_lizard(avail, spot, put_otm, call_otm, call_wing):
    """Naked short put + short call spread. No upside risk if credit > call wing width."""
    sp = find_strike(avail, spot * (1 - put_otm))
    sc = find_strike(avail, spot * (1 + call_otm))
    if sp is None or sc is None: return None
    lc = find_strike(avail, sc + call_wing)
    if lc is None: return None
    return [(sp, "P", -1), (sc, "C", -1), (lc, "C", +1)]


def big_lizard(avail, spot, wing):
    """ATM short put + ATM short call + call wing only (no put wing)."""
    atm = find_strike(avail, spot)
    if atm is None: return None
    lc = find_strike(avail, atm + wing)
    if lc is None: return None
    return [(atm, "P", -1), (atm, "C", -1), (lc, "C", +1)]


# ─── Run sweeps ────────────────────────────────────────────────────────────
log("\n" + "=" * 100)
log("BASELINE: Iron Fly 40pt symmetric (09:45/15:45, 2.0x SL, 50% PT)")
log("=" * 100)
baseline = run("Iron Fly 40pt", iron_fly, wing=40)
log(f"  N={baseline['n']}, Win={baseline['win_rate']:.0f}%, P&L=${baseline['total_pnl']:+,.0f}, Avg=${baseline['avg_pnl']:+.0f}, Worst=${baseline['worst']:+.0f}")

log("\n" + "=" * 100)
log("BPS (Bull Put Spread — one-sided put only)")
log("=" * 100)
log(f"{'OTM%':<7}{'Wing':<7}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<9}")
log("-" * 80)
bps_results = []
for otm in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05]:
    for w in [5, 10, 15, 20, 25, 40, 50]:
        r = run(f"BPS {otm*100}% / {w}pt", bps, otm_pct=otm, wing=w)
        if r:
            r["otm"] = otm; r["wing"] = w
            bps_results.append(r)
for r in sorted(bps_results, key=lambda x: -x["total_pnl"])[:12]:
    log(f"{r['otm']*100:<6.1f}%{r['wing']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+8.0f}")

log("\n" + "=" * 100)
log("BCS (Bear Call Spread — one-sided call only)")
log("=" * 100)
log(f"{'OTM%':<7}{'Wing':<7}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<9}")
log("-" * 80)
bcs_results = []
for otm in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05]:
    for w in [5, 10, 15, 20, 25, 40, 50]:
        r = run(f"BCS {otm*100}% / {w}pt", bcs, otm_pct=otm, wing=w)
        if r:
            r["otm"] = otm; r["wing"] = w
            bcs_results.append(r)
for r in sorted(bcs_results, key=lambda x: -x["total_pnl"])[:12]:
    log(f"{r['otm']*100:<6.1f}%{r['wing']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+8.0f}")

log("\n" + "=" * 100)
log("BPS + BCS COMBINED (run as two separate trades same day — different legs)")
log("=" * 100)
# Find best BPS and best BCS, then sum their P&L (assumes you run BOTH each day)
best_bps = sorted(bps_results, key=lambda x: -x["total_pnl"])[0] if bps_results else None
best_bcs = sorted(bcs_results, key=lambda x: -x["total_pnl"])[0] if bcs_results else None
if best_bps and best_bcs:
    combined_pnl = best_bps["total_pnl"] + best_bcs["total_pnl"]
    log(f"  Best BPS: ${best_bps['total_pnl']:+,.0f} ({best_bps['otm']*100:.1f}% / {best_bps['wing']}pt)")
    log(f"  Best BCS: ${best_bcs['total_pnl']:+,.0f} ({best_bcs['otm']*100:.1f}% / {best_bcs['wing']}pt)")
    log(f"  Combined hypothetical: ${combined_pnl:+,.0f}")
    log(f"  vs Iron Fly 40pt: ${baseline['total_pnl']:+,.0f}")
    log(f"  (Capital is 2x though — running both sides means 2 separate positions)")

log("\n" + "=" * 100)
log("IRON CONDOR with various OTM (testing 0.5%, 1%, 2% vs ATM Iron Fly)")
log("=" * 100)
log(f"{'OTM%':<7}{'Wing':<7}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<9}")
log("-" * 80)
ic_results = []
for otm in [0.005, 0.01, 0.015, 0.02]:
    for w in [15, 25, 40]:
        r = run(f"IC {otm*100}% / {w}pt", iron_condor, otm_put=otm, otm_call=otm, wing=w)
        if r:
            r["otm"] = otm; r["wing"] = w
            ic_results.append(r)
for r in sorted(ic_results, key=lambda x: -x["total_pnl"])[:10]:
    log(f"{r['otm']*100:<6.1f}%{r['wing']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+8.0f}${r['worst']:<+8.0f}")

log("\n" + "=" * 100)
log("JADE LIZARD (naked short put + call spread, NO PUT WING)")
log("=" * 100)
log(f"{'P-OTM%':<8}{'C-OTM%':<8}{'CWing':<7}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'Best':<9}{'Worst':<10}")
log("-" * 85)
jl_results = []
for po in [0.005, 0.01, 0.02]:
    for co in [0.005, 0.01, 0.02]:
        for cw in [15, 25, 40]:
            r = run(f"JL", jade_lizard, put_otm=po, call_otm=co, call_wing=cw)
            if r:
                r["po"] = po; r["co"] = co; r["cw"] = cw
                jl_results.append(r)
for r in sorted(jl_results, key=lambda x: -x["total_pnl"])[:10]:
    log(f"{r['po']*100:<7.1f}%{r['co']*100:<7.1f}%{r['cw']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

log("\n" + "=" * 100)
log("BIG LIZARD (ATM straddle + call wing only)")
log("=" * 100)
log(f"{'Wing':<7}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'Best':<9}{'Worst':<10}")
log("-" * 75)
bl_results = []
for w in [15, 25, 40, 60, 80]:
    r = run(f"BL {w}pt", big_lizard, wing=w)
    if r:
        r["wing"] = w
        bl_results.append(r)
for r in sorted(bl_results, key=lambda x: -x["total_pnl"])[:8]:
    log(f"{r['wing']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

log("\n" + "=" * 100)
log("SLIGHT-OTM IRON FLY (small offset from ATM)")
log("=" * 100)
log(f"{'Offset':<8}{'Wing':<7}{'N':<5}{'Win%':<7}{'TotalP&L':<11}{'Best':<9}{'Worst':<10}")
log("-" * 75)
of_results = []
for off in [-0.01, -0.005, 0, 0.005, 0.01]:
    for w in [30, 40, 50]:
        r = run(f"IF off={off}", iron_fly, wing=w, offset_pct=off)
        if r:
            r["offset"] = off; r["wing"] = w
            of_results.append(r)
for r in sorted(of_results, key=lambda x: -x["total_pnl"])[:10]:
    log(f"{r['offset']*100:<+7.1f}%{r['wing']:<7}{r['n']:<5}{r['win_rate']:<6.0f}%${r['total_pnl']:<+10,.0f}${r['best']:<+8.0f}${r['worst']:<+9.0f}")

log(f"\n[BASELINE for comparison: Iron Fly 40pt = ${baseline['total_pnl']:+,.0f}]")
