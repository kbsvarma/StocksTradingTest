"""Thorough strategy sweep on AVGO 2026 M/W/F 0DTE data.

Tests:
 A. Iron Condor — symmetric (baseline)
 B. Iron Condor — asymmetric (different OTM put vs call)
 C. Bull Put Spread only (BPS) — one-sided
 D. Bear Call Spread only (BCS) — one-sided
 E. Iron Fly — ATM short + wider wings
 F. Exit rule variants on best IC config
 G. Entry time variants
 H. Profit target / stop loss multiplier variants

All results reported. No cherry-picking.
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

def log(m): print(m, flush=True)


# ─── Load 2026 data ─────────────────────────────────────────────────────────
log("Loading 2026 parquets...")
t0 = time.time()
files_2026 = sorted(DATA_DIR.glob("avgo_cbbo_2026*.parquet"))
log(f"  {len(files_2026)} files")

expiry_index = defaultdict(list)
strikes_by_day = defaultdict(lambda: defaultdict(set))

for f in files_2026:
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

trade_dates = sorted(strikes_by_day.keys())
log(f"  Indexed ({time.time()-t0:.0f}s, {len(expiry_index)} keys, {len(trade_dates)} dates)")

# AVGO spot
spot = yf.Ticker("AVGO").history(start="2026-02-01", end="2026-06-01")
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}


# ─── Helpers ────────────────────────────────────────────────────────────────
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


# ─── Generic spread simulator ───────────────────────────────────────────────
def simulate_legs(td, target_exp, legs_spec, spot_exp_close, entry_ts, exit_ts,
                  stop_mult=2.0, pt_frac=0.5):
    """legs_spec: list of (strike, right, side) where side in (+1=long, -1=short)."""
    leg_data = []
    entry_credit = 0  # signed: positive = credit received
    for (strike, right, side) in legs_spec:
        leg_df = get_leg(target_exp, strike, right)
        if leg_df is None: return None
        e = at_or_after(leg_df, entry_ts)
        if e is None: return None
        if side == -1:  # short = sell at bid
            entry_credit += float(e["bid_px_00"])
        else:  # long = buy at ask
            entry_credit -= float(e["ask_px_00"])
        leg_data.append({"strike": strike, "right": right, "side": side, "df": leg_df})

    if entry_credit < 0.10:  # net debit or trivially small credit
        return None

    # Compute max loss at expiry: worst-case strike combinations
    # For IC: max loss = wing_width - credit
    # For BPS: max loss = width_put - credit
    # For BCS: max loss = width_call - credit
    # Generic: scan a wide spot range to find min payoff
    strikes_in_play = [l["strike"] for l in leg_data]
    if not strikes_in_play: return None
    test_lo = min(strikes_in_play) - 20
    test_hi = max(strikes_in_play) + 20
    min_pnl = float('inf')
    for test in [test_lo, test_hi] + [int(s) for s in strikes_in_play]:
        v = 0
        for l in leg_data:
            if l["right"] == "P":
                intrinsic = max(0, l["strike"] - test)
            else:
                intrinsic = max(0, test - l["strike"])
            v += l["side"] * intrinsic
        # P&L = credit + settle_value
        pnl_test = entry_credit + v
        min_pnl = min(min_pnl, pnl_test)
    max_loss = -min(min_pnl, 0) * 100

    stop_loss_dollars = entry_credit * stop_mult
    pt_dollars = entry_credit * (1 - pt_frac)

    # Walk minute by minute for SL/PT
    # Compute current debit-to-close at each minute
    # Build aligned frame
    frames = []
    for l in leg_data:
        win = l["df"].loc[(l["df"].index >= entry_ts) & (l["df"].index <= exit_ts)]
        if win.empty: continue
        cols = {f"bid_{l['strike']}_{l['right']}": win["bid_px_00"],
                f"ask_{l['strike']}_{l['right']}": win["ask_px_00"]}
        frames.append(pd.DataFrame(cols))

    exit_val = None
    exit_reason = None

    if len(frames) == len(leg_data):
        try:
            al = frames[0]
            for fr in frames[1:]:
                al = al.join(fr, how="inner")
            if not al.empty:
                # Compute debit to close at each timestamp
                # For each leg: to close short → pay ask; to close long → receive bid
                debit_to_close = pd.Series(0.0, index=al.index)
                for l in leg_data:
                    bid_col = f"bid_{l['strike']}_{l['right']}"
                    ask_col = f"ask_{l['strike']}_{l['right']}"
                    if l["side"] == -1:  # short: pay ask to close
                        debit_to_close += al[ask_col]
                    else:  # long: receive bid (so subtract)
                        debit_to_close -= al[bid_col]

                sl_hits = al[debit_to_close >= stop_loss_dollars]
                pt_hits = al[debit_to_close <= pt_dollars]
                sl_ts = sl_hits.index.min() if not sl_hits.empty else None
                pt_ts = pt_hits.index.min() if not pt_hits.empty else None
                if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                    exit_val = debit_to_close.loc[sl_ts]
                    exit_reason = "stop_loss"
                elif pt_ts is not None:
                    exit_val = debit_to_close.loc[pt_ts]
                    exit_reason = "profit_target"
        except Exception:
            pass

    if exit_val is None:
        # Settle at expiry
        settle_payoff = 0
        for l in leg_data:
            if l["right"] == "P":
                intrinsic = max(0, l["strike"] - spot_exp_close)
            else:
                intrinsic = max(0, spot_exp_close - l["strike"])
            settle_payoff += -l["side"] * intrinsic  # if short, settle = -intrinsic (we owe)
                                                      # if long, settle = +intrinsic (we collect)
        # debit to close = -settle_payoff (close out by paying back net intrinsic)
        # Wait — if short put goes ITM, we LOSE the intrinsic. So debit_to_close = sum of intrinsics owed
        # Let me redo: total close cost = sum_legs(side==-1: +intrinsic, side==+1: -intrinsic)
        exit_val = 0
        for l in leg_data:
            if l["right"] == "P":
                intrinsic = max(0, l["strike"] - spot_exp_close)
            else:
                intrinsic = max(0, spot_exp_close - l["strike"])
            if l["side"] == -1:  # short: we owe intrinsic
                exit_val += intrinsic
            else:  # long: we collect intrinsic
                exit_val -= intrinsic
        exit_reason = "expiry"

    pnl = (entry_credit - exit_val) * 100
    return {
        "credit": entry_credit, "credit_dollars": entry_credit * 100,
        "max_loss_dollars": max_loss,
        "exit_value": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


# ─── Strategy builders ──────────────────────────────────────────────────────
def build_ic(avail, spot, otm_put, otm_call, wing_put, wing_call):
    sp = find_strike(avail, spot * (1 - otm_put))
    sc = find_strike(avail, spot * (1 + otm_call))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing_put)
    lc = find_strike(avail, sc + wing_call)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (sp, "P", -1), (sc, "C", -1), (lc, "C", +1)]


def build_bps(avail, spot, otm, wing):
    sp = find_strike(avail, spot * (1 - otm))
    if sp is None: return None
    lp = find_strike(avail, sp - wing)
    if lp is None: return None
    return [(lp, "P", +1), (sp, "P", -1)]


def build_bcs(avail, spot, otm, wing):
    sc = find_strike(avail, spot * (1 + otm))
    if sc is None: return None
    lc = find_strike(avail, sc + wing)
    if lc is None: return None
    return [(sc, "C", -1), (lc, "C", +1)]


def build_iron_fly(avail, spot, wing):
    """Iron Fly: short put AND short call at ATM, with wings."""
    atm = find_strike(avail, spot)
    if atm is None: return None
    lp = find_strike(avail, atm - wing)
    lc = find_strike(avail, atm + wing)
    if lp is None or lc is None: return None
    return [(lp, "P", +1), (atm, "P", -1), (atm, "C", -1), (lc, "C", +1)]


# ─── Runner ─────────────────────────────────────────────────────────────────
def run_strategy(name, builder_fn, builder_args_list, entry_hour=10, entry_min=30,
                 stop_mult=2.0, pt_frac=0.5):
    """Run a strategy across all 0DTE M/W/F days. Returns list of result dicts."""
    results = []
    for td in trade_dates:
        if td.weekday() not in (0, 2, 4): continue  # Mon/Wed/Fri only
        if td not in spot_by_date: continue
        # 0DTE only
        if td not in strikes_by_day[td]: continue

        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"])
        spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)

        entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=entry_hour, minute=entry_min).astimezone(UTC))
        exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=16, minute=0).astimezone(UTC))

        avail = strikes_by_day[td][td]
        for args_dict in builder_args_list:
            legs = builder_fn(avail, spot_entry, **args_dict)
            if legs is None: continue
            sim = simulate_legs(td, td, legs, spot_c, entry_ts, exit_ts,
                                stop_mult=stop_mult, pt_frac=pt_frac)
            if sim is None: continue
            row = {**args_dict, "trade_date": td, "dow": td.strftime("%a"),
                   "strategy": name, **sim}
            results.append(row)
    return results


def summarize(results, label, group_keys):
    df = pd.DataFrame(results)
    if df.empty: return pd.DataFrame()
    rows = []
    for g, sub in df.groupby(group_keys):
        if len(sub) < 5: continue
        n = len(sub)
        wins = (sub["pnl"] > 0).sum()
        wr = wins/n*100
        total = sub["pnl"].sum()
        avg = sub["pnl"].mean()
        best = sub["pnl"].max()
        worst = sub["pnl"].min()
        avg_cr = sub["credit_dollars"].mean()
        stops = (sub["exit_reason"] == "stop_loss").sum()
        if isinstance(g, tuple):
            g_dict = dict(zip(group_keys, g))
        else:
            g_dict = {group_keys[0]: g}
        rows.append({"strategy": label, **g_dict, "n": n, "wins": wins, "win_rate": wr,
                     "avg_credit": avg_cr, "total_pnl": total, "avg_pnl": avg,
                     "best": best, "worst": worst, "stops": stops})
    return pd.DataFrame(rows)


# ─── Run all strategies ─────────────────────────────────────────────────────
log("\n" + "=" * 100)
log("STRATEGY SWEEP — AVGO 2026 M/W/F 0DTE (Mar 2 - May 20, 2026)")
log("=" * 100)

all_summaries = []

# A. Iron Condor — symmetric (baseline expansion)
log("\nA. Iron Condor — symmetric (fine-grained)")
ic_args = [{"otm_put": p, "otm_call": p, "wing_put": w, "wing_call": w}
           for p in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
           for w in [5, 7.5, 10, 12.5, 15, 20, 25, 30]]
results_a = run_strategy("IC_sym", build_ic, ic_args)
sum_a = summarize(results_a, "IC_sym", ["otm_put", "wing_put"])
all_summaries.append(sum_a)
log(f"   {len(results_a)} simulations, {len(sum_a)} variants with N>=5")

# B. Iron Condor — asymmetric
log("\nB. Iron Condor — asymmetric (different OTM put vs call)")
asym_args = []
for op in [0.01, 0.015, 0.02, 0.025, 0.03]:
    for oc in [0.01, 0.015, 0.02, 0.025, 0.03, 0.04]:
        if op == oc: continue
        for w in [10, 15, 25]:
            asym_args.append({"otm_put": op, "otm_call": oc, "wing_put": w, "wing_call": w})
results_b = run_strategy("IC_asym", build_ic, asym_args)
sum_b = summarize(results_b, "IC_asym", ["otm_put", "otm_call", "wing_put"])
all_summaries.append(sum_b)
log(f"   {len(results_b)} simulations, {len(sum_b)} variants")

# C. Bull Put Spread only
log("\nC. Bull Put Spread (one-sided)")
bps_args = [{"otm": p, "wing": w}
            for p in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
            for w in [5, 10, 15, 25]]
results_c = run_strategy("BPS", build_bps, bps_args)
sum_c = summarize(results_c, "BPS", ["otm", "wing"])
all_summaries.append(sum_c)
log(f"   {len(results_c)} simulations, {len(sum_c)} variants")

# D. Bear Call Spread only
log("\nD. Bear Call Spread (one-sided)")
bcs_args = [{"otm": p, "wing": w}
            for p in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]
            for w in [5, 10, 15, 25]]
results_d = run_strategy("BCS", build_bcs, bcs_args)
sum_d = summarize(results_d, "BCS", ["otm", "wing"])
all_summaries.append(sum_d)
log(f"   {len(results_d)} simulations, {len(sum_d)} variants")

# E. Iron Fly — ATM short + wings
log("\nE. Iron Fly (ATM short + wider wings)")
fly_args = [{"wing": w} for w in [5, 10, 15, 20, 25, 30, 40, 50]]
results_e = run_strategy("IronFly", build_iron_fly, fly_args)
sum_e = summarize(results_e, "IronFly", ["wing"])
all_summaries.append(sum_e)
log(f"   {len(results_e)} simulations, {len(sum_e)} variants")

# F. Exit rule variants on best IC (2% OTM / 25pt)
log("\nF. Exit rule variants on 2% OTM / 25pt IC")
best_ic = [{"otm_put": 0.02, "otm_call": 0.02, "wing_put": 25, "wing_call": 25}]
exit_variants = []
for sm in [1.5, 2.0, 2.5, 3.0, 5.0]:  # 5.0 ≈ no stop
    for pt in [0.25, 0.5, 0.75, 0.95]:  # 0.95 ≈ never hit PT
        results_f = run_strategy(f"IC_SL{sm}_PT{pt}", build_ic, best_ic,
                                  stop_mult=sm, pt_frac=pt)
        if results_f:
            sub = pd.DataFrame(results_f)
            n = len(sub); wins = (sub["pnl"] > 0).sum()
            exit_variants.append({
                "stop_mult": sm, "pt_frac": pt, "n": n,
                "win_rate": wins/n*100,
                "total_pnl": sub["pnl"].sum(),
                "avg_pnl": sub["pnl"].mean(),
                "worst": sub["pnl"].min(),
            })
sum_f = pd.DataFrame(exit_variants)
log(f"   {len(sum_f)} exit rule combos tested")

# G. Entry time variants on best IC (2% OTM / 25pt)
log("\nG. Entry time variants")
entry_variants = []
for hr, mn in [(9, 35), (10, 0), (10, 30), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0)]:
    results_g = run_strategy(f"IC_{hr:02d}{mn:02d}", build_ic, best_ic,
                              entry_hour=hr, entry_min=mn)
    if results_g:
        sub = pd.DataFrame(results_g)
        n = len(sub); wins = (sub["pnl"] > 0).sum()
        entry_variants.append({
            "entry": f"{hr:02d}:{mn:02d}", "n": n,
            "win_rate": wins/n*100,
            "total_pnl": sub["pnl"].sum(),
            "avg_pnl": sub["pnl"].mean(),
            "worst": sub["pnl"].min(),
            "avg_credit": sub["credit_dollars"].mean(),
        })
sum_g = pd.DataFrame(entry_variants)
log(f"   {len(sum_g)} entry times tested")


# ─── Report top from each category ──────────────────────────────────────────
log("\n" + "=" * 100)
log("TOP 10 BY TOTAL P&L — IRON CONDOR (symmetric)")
log("=" * 100)
log(sum_a.sort_values("total_pnl", ascending=False).head(10).to_string(index=False))

log("\n" + "=" * 100)
log("TOP 10 BY TOTAL P&L — IRON CONDOR (asymmetric)")
log("=" * 100)
log(sum_b.sort_values("total_pnl", ascending=False).head(10).to_string(index=False))

log("\n" + "=" * 100)
log("BULL PUT SPREAD ONLY (no call side)")
log("=" * 100)
log(sum_c.sort_values("total_pnl", ascending=False).head(10).to_string(index=False))

log("\n" + "=" * 100)
log("BEAR CALL SPREAD ONLY (no put side)")
log("=" * 100)
log(sum_d.sort_values("total_pnl", ascending=False).head(10).to_string(index=False))

log("\n" + "=" * 100)
log("IRON FLY (ATM short)")
log("=" * 100)
log(sum_e.sort_values("total_pnl", ascending=False).to_string(index=False))

log("\n" + "=" * 100)
log("EXIT RULE VARIANTS (on 2% OTM / 25pt IC)")
log("=" * 100)
log(sum_f.sort_values("total_pnl", ascending=False).to_string(index=False))

log("\n" + "=" * 100)
log("ENTRY TIME VARIANTS (on 2% OTM / 25pt IC)")
log("=" * 100)
log(sum_g.sort_values("total_pnl", ascending=False).to_string(index=False))

# Compare to baseline
log("\n" + "=" * 100)
log("BASELINE: 2% OTM / 25pt symmetric IC at 10:30, 2x SL, 50% PT, M+W+F 0DTE")
baseline_results = run_strategy("IC_baseline", build_ic, best_ic)
b = pd.DataFrame(baseline_results)
log(f"  N={len(b)}, win={ (b['pnl']>0).sum()}/{len(b)} ({(b['pnl']>0).mean()*100:.0f}%), total=${b['pnl'].sum():+,.0f}")

# Save all
for i, s in enumerate(all_summaries):
    s.to_csv(f"/tmp/avgo_thorough_{i}.csv", index=False)
sum_f.to_csv("/tmp/avgo_thorough_exits.csv", index=False)
sum_g.to_csv("/tmp/avgo_thorough_entry_times.csv", index=False)
log("\nAll CSVs saved to /tmp/avgo_thorough_*.csv")
