"""IWM IFLY verification v3 — realistic combo slippage modeling.

Same fix as AVGO v3:
- Use mid pricing instead of natural
- Apply combo-level slippage ($0.05-0.10 each way), NOT per-leg
- Use real bid/ask at 15:45 (not intrinsic at 4PM)
- NaN bid on worthless long wings treated as $0
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data" / "iwm_wide"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 1.0

def log(m): print(m, flush=True)


log("Loading IWM wide data (this takes ~3 min)...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
files = sorted(DATA.glob("iwm_cbbo_*.parquet"))
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
log(f"Loaded ({time.time()-t0:.0f}s)")

spot_hist = pd.read_parquet("/tmp/iwm_spot_cache.parquet")

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
    if df is None or df.empty: return None, None
    window = pd.Timedelta(minutes=window_minutes)
    before = df.loc[df.index <= ts]
    if not before.empty:
        last = before.iloc[-1]
        if last.name >= ts - window:
            return float(last["bid_px_00"]), float(last["ask_px_00"])
    after = df.loc[df.index > ts]
    if not after.empty:
        first = after.iloc[0]
        if first.name <= ts + window:
            return float(first["bid_px_00"]), float(first["ask_px_00"])
    return None, None


def simulate_iron_fly_v3(td, wing_pct, exit_hour, exit_min,
                         combo_entry_slip=0.0, combo_exit_slip=0.0,
                         use_mid_entry=False):
    if td not in spot_by_date: return {"status": "no_spot"}
    if td not in strikes_by_day[td]: return {"status": "no_0dte"}
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td][td]
    atm = find_strike(avail, spot_entry)
    if atm is None: return {"status": "no_atm"}
    wing = spot_entry * wing_pct
    lp = find_strike(avail, atm - wing)
    lc = find_strike(avail, atm + wing)
    if lp is None or lc is None: return {"status": "no_wings"}

    sp_df = expiry_index.get((td, td, atm, "P"))
    sc_df = expiry_index.get((td, td, atm, "C"))
    lp_df = expiry_index.get((td, td, lp, "P"))
    lc_df = expiry_index.get((td, td, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]):
        return {"status": "no_leg_data"}

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=exit_hour, minute=exit_min).astimezone(UTC))

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]):
        return {"status": "no_entry"}

    sp_bid = float(e_sp["bid_px_00"]); sp_ask = float(e_sp["ask_px_00"])
    sc_bid = float(e_sc["bid_px_00"]); sc_ask = float(e_sc["ask_px_00"])
    lp_bid = float(e_lp["bid_px_00"]) if pd.notna(e_lp["bid_px_00"]) else 0
    lp_ask = float(e_lp["ask_px_00"]) if pd.notna(e_lp["ask_px_00"]) else 0.01
    lc_bid = float(e_lc["bid_px_00"]) if pd.notna(e_lc["bid_px_00"]) else 0
    lc_ask = float(e_lc["ask_px_00"]) if pd.notna(e_lc["ask_px_00"]) else 0.01

    if pd.isna(sp_bid) or pd.isna(sp_ask) or pd.isna(sc_bid) or pd.isna(sc_ask):
        return {"status": "no_short_quotes"}

    if use_mid_entry:
        sp_mid = (sp_bid + sp_ask) / 2
        sc_mid = (sc_bid + sc_ask) / 2
        lp_mid = (lp_bid + lp_ask) / 2
        lc_mid = (lc_bid + lc_ask) / 2
        credit = sp_mid + sc_mid - lp_mid - lc_mid - combo_entry_slip
    else:
        credit = sp_bid + sc_bid - lp_ask - lc_ask - combo_entry_slip

    if credit < 0.05: return {"status": "thin_credit"}

    stop_debit = credit * 2.0
    pt_debit = credit * 0.5

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    exit_val = None; exit_reason = None
    if not any(s.empty for s in [sp_w, sc_w]):
        sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"}).copy()
        lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"}).copy()
        lp_r["b_lp"] = lp_r["b_lp"].fillna(0)
        lc_r["b_lc"] = lc_r["b_lc"].fillna(0)
        al = sp_r.join(sc_r, how="inner").join(lp_r[["b_lp"]], how="left").join(lc_r[["b_lc"]], how="left")
        al["b_lp"] = al["b_lp"].fillna(0)
        al["b_lc"] = al["b_lc"].fillna(0)
        al = al.dropna(subset=["a_sp", "a_sc"])
        if not al.empty:
            al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
            sl = al[al["debit"] >= stop_debit]
            pt = al[al["debit"] <= pt_debit]
            sl_ts = sl.index.min() if not sl.empty else None
            pt_ts = pt.index.min() if not pt.empty else None
            if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
                exit_val = al.loc[sl_ts, "debit"] + combo_exit_slip
                exit_reason = "stop_loss"
            elif pt_ts is not None:
                exit_val = al.loc[pt_ts, "debit"] + combo_exit_slip
                exit_reason = "profit_target"

    if exit_val is None:
        sp_b, sp_a = get_quote_at_or_near(sp_df, exit_ts)
        sc_b, sc_a = get_quote_at_or_near(sc_df, exit_ts)
        lp_b, lp_a = get_quote_at_or_near(lp_df, exit_ts)
        lc_b, lc_a = get_quote_at_or_near(lc_df, exit_ts)
        if sp_a is None or sc_a is None or pd.isna(sp_a) or pd.isna(sc_a):
            return {"status": "no_short_ask_exit"}
        lp_b_safe = lp_b if (lp_b is not None and not pd.isna(lp_b)) else 0
        lc_b_safe = lc_b if (lc_b is not None and not pd.isna(lc_b)) else 0

        if use_mid_entry:
            sp_b_mid = (sp_b + sp_a) / 2 if (sp_b is not None and not pd.isna(sp_b)) else sp_a
            sc_b_mid = (sc_b + sc_a) / 2 if (sc_b is not None and not pd.isna(sc_b)) else sc_a
            lp_b_mid = (lp_b_safe + (lp_a if (lp_a is not None and not pd.isna(lp_a)) else lp_b_safe)) / 2
            lc_b_mid = (lc_b_safe + (lc_a if (lc_a is not None and not pd.isna(lc_a)) else lc_b_safe)) / 2
            exit_val = sp_b_mid + sc_b_mid - lp_b_mid - lc_b_mid + combo_exit_slip
        else:
            exit_val = sp_a + sc_a - lp_b_safe - lc_b_safe + combo_exit_slip
        exit_reason = "forced_close"

    if exit_val is None or pd.isna(exit_val):
        return {"status": "nan_exit"}

    pnl = (credit - exit_val) * 100
    return {
        "status": "ok",
        "trade_date": td, "dow": td.strftime("%a"), "year": td.year,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_val": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


def run(exit_hour, exit_min, entry_slip, exit_slip, use_mid):
    results = []
    statuses = defaultdict(int)
    for td in sorted(strikes_by_day.keys()):
        r = simulate_iron_fly_v3(td, 0.08, exit_hour, exit_min, entry_slip, exit_slip, use_mid)
        statuses[r["status"]] += 1
        if r["status"] == "ok":
            results.append(r)
    return pd.DataFrame(results), dict(statuses)


log("\n" + "=" * 110)
log("IWM IFLY 8% wings — V3 with realistic combo slippage")
log("=" * 110)

scenarios = [
    ("A. Natural, no slip",                  0.00, 0.00, False),
    ("B. Natural + $0.05 combo each way",    0.05, 0.05, False),
    ("C. Natural + $0.10 combo each way",    0.10, 0.10, False),
    ("D. Mid + $0.05 combo each way",        0.05, 0.05, True),
    ("E. Mid + $0.10 combo each way",        0.10, 0.10, True),
]

# Test at 15:45 (standard close)
log("\n--- 15:45 exit ---")
log(f"{'Scenario':<45}{'N':<6}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Worst':<9}{'Annual':<12}")
log("-" * 100)
for label, e_slip, x_slip, mid in scenarios:
    df, _ = run(15, 45, e_slip, x_slip, mid)
    if df.empty: continue
    n = len(df); wins = (df["pnl"] > 0).sum()
    total = df["pnl"].sum()
    annual = total / 2.5  # 2.5 year sample
    log(f"{label:<45}{n:<6}{wins/n*100:<6.0f}%${total:<+10,.0f}${df['pnl'].mean():<+8.0f}${df['pnl'].min():<+8.0f}${annual:<+11,.0f}")

# Test multiple exit times for the best scenario (mid + $0.05)
log("\n--- Best scenario (Mid + $0.05 combo) across exit times ---")
log(f"{'Exit':<8}{'N':<6}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Worst':<9}{'Annual':<12}")
log("-" * 75)
for h, m in [(14, 0), (14, 30), (15, 0), (15, 15), (15, 30), (15, 45)]:
    df, _ = run(h, m, 0.05, 0.05, True)
    if df.empty: continue
    n = len(df); wins = (df["pnl"] > 0).sum()
    total = df["pnl"].sum()
    annual = total / 2.5
    log(f"{h:02d}:{m:02d}    {n:<6}{wins/n*100:<6.0f}%${total:<+10,.0f}${df['pnl'].mean():<+8.0f}${df['pnl'].min():<+8.0f}${annual:<+11,.0f}")

# Per-year breakdown for the best mid scenario
log("\n--- Per-year breakdown (Mid + $0.05 combo, 15:00 exit) ---")
df, _ = run(15, 0, 0.05, 0.05, True)
log(f"{'Year':<8}{'N':<6}{'Win%':<7}{'Total':<11}{'Avg':<9}{'Worst':<9}")
log("-" * 60)
for year in sorted(df["year"].unique()):
    sub = df[df["year"] == year]
    n = len(sub); wins = (sub["pnl"] > 0).sum()
    log(f"{year:<8}{n:<6}{wins/n*100:<6.0f}%${sub['pnl'].sum():<+10,.0f}${sub['pnl'].mean():<+8.0f}${sub['pnl'].min():<+8.0f}")

# Per day-of-week
log("\n--- Per day-of-week breakdown (Mid + $0.05, 15:00 exit) ---")
log(f"{'Day':<6}{'N':<6}{'Win%':<7}{'Total':<11}{'Avg':<9}")
log("-" * 50)
for dow in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
    sub = df[df["dow"] == dow]
    if sub.empty: continue
    n = len(sub); wins = (sub["pnl"] > 0).sum()
    log(f"{dow:<6}{n:<6}{wins/n*100:<6.0f}%${sub['pnl'].sum():<+10,.0f}${sub['pnl'].mean():<+8.0f}")

# Breakeven slippage
log("\n--- Breakeven analysis (mid pricing, 15:00 exit) ---")
log("(At what combo slippage does the strategy go negative?)")
for slip in [0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75, 1.00]:
    df, _ = run(15, 0, slip, slip, True)
    if df.empty: continue
    total = df["pnl"].sum()
    annual = total / 2.5
    flag = " ← BREAKEVEN" if abs(total) < 500 else ""
    log(f"  each-way ${slip:.2f} (RT ${slip*2:.2f}): total=${total:<+9,.0f}  annual=${annual:<+8,.0f}{flag}")
