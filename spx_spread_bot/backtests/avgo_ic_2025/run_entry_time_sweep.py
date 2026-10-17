"""Entry time sweep for Iron Fly 40pt wings on AVGO M/W/F 0DTE."""
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
log(f"  Loaded ({time.time()-t0:.0f}s)")


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


def simulate_iron_fly(td, target_exp, wing, spot_entry, spot_exp_close, entry_ts, exit_ts):
    avail = strikes_by_day[td].get(target_exp, set())
    if not avail: return None
    atm = find_strike(avail, spot_entry)
    if atm is None: return None
    lp = find_strike(avail, atm - wing)
    lc = find_strike(avail, atm + wing)
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

    stop_debit = credit * 2.0
    pt_debit = credit * 0.5

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
    return {"credit": credit, "credit_dollars": credit*100,
            "exit_reason": exit_reason, "pnl": pnl, "atm": atm}


# Test many entry times
ENTRY_TIMES = [
    (9, 32), (9, 35), (9, 45), (10, 0), (10, 15), (10, 30), (10, 45),
    (11, 0), (11, 30), (12, 0), (12, 30), (13, 0), (13, 30), (14, 0),
    (14, 15), (14, 30), (14, 45), (15, 0)
]

# Test multiple exit times too for completeness
EXIT_TIMES = [(15, 45), (15, 30), (15, 0), (16, 0)]

log("\nRunning entry × exit time sweep on Iron Fly 40pt...")
results = []
for hr, mn in ENTRY_TIMES:
    for ex_hr, ex_mn in EXIT_TIMES:
        if (ex_hr, ex_mn) <= (hr, mn): continue  # exit must be after entry
        trades = []
        for td in sorted(strikes_by_day.keys()):
            if td.weekday() not in (0, 2, 4): continue
            if td not in spot_by_date: continue
            if td not in strikes_by_day[td]: continue
            spot_row = spot_by_date[td]
            spot_o = float(spot_row["Open"])
            spot_c = float(spot_row["Close"])
            spot_entry = spot_o + 0.3 * (spot_c - spot_o)
            entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
                hour=hr, minute=mn).astimezone(UTC))
            exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
                hour=ex_hr, minute=ex_mn).astimezone(UTC))
            r = simulate_iron_fly(td, td, 40, spot_entry, spot_c, entry_ts, exit_ts)
            if r is not None:
                trades.append(r)
        if trades:
            df = pd.DataFrame(trades)
            results.append({
                "entry": f"{hr:02d}:{mn:02d}",
                "exit": f"{ex_hr:02d}:{ex_mn:02d}",
                "n": len(df),
                "win_rate": (df["pnl"] > 0).mean() * 100,
                "avg_credit": df["credit_dollars"].mean(),
                "total_pnl": df["pnl"].sum(),
                "avg_pnl": df["pnl"].mean(),
                "best": df["pnl"].max(),
                "worst": df["pnl"].min(),
                "stops": (df["exit_reason"] == "stop_loss").sum(),
                "pts": (df["exit_reason"] == "profit_target").sum(),
            })

summary = pd.DataFrame(results)
summary.to_csv("/tmp/avgo_iron_fly_entry_times.csv", index=False)

# Show all results sorted by total P&L
log("\n" + "=" * 110)
log("IRON FLY 40pt — ENTRY × EXIT TIME SWEEP (sorted by total P&L)")
log("=" * 110)
log(f"{'Entry':<8}{'Exit':<8}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<8}{'Worst':<9}{'Stops':<7}{'PT':<5}")
log("-" * 95)
for _, r in summary.sort_values("total_pnl", ascending=False).iterrows():
    log(f"{r['entry']:<8}{r['exit']:<8}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+7.0f}${r['worst']:<+8.0f}{int(r['stops']):<7}{int(r['pts']):<5}")

# Focus: fixed exit at 15:45, vary entry
log("\n" + "=" * 110)
log("FIXED EXIT 15:45 — vary entry time")
log("=" * 110)
sub = summary[summary["exit"] == "15:45"].sort_values("entry")
log(f"{'Entry':<8}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<8}{'Worst':<9}")
log("-" * 75)
for _, r in sub.iterrows():
    log(f"{r['entry']:<8}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+7.0f}${r['worst']:<+8.0f}")

log("\nDone")
