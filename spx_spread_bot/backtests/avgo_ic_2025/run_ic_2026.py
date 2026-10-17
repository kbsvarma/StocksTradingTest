"""AVGO IC backtest on March-May 2026 data with M/W/F 0DTE structure.

Specifically tests:
- 0DTE trades segmented by day-of-week (Mon vs Wed vs Fri)
- Multi-DTE bucket performance
- Comparison to 2025 Friday-only baseline
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
ENTRY_HOUR_ET = 10
ENTRY_MIN_ET = 30
STOP_MULT = 2.0
PT_FRAC = 0.5

def log(m): print(m, flush=True)


# ─── Load 2026 data only ────────────────────────────────────────────────────
log("Loading 2026 parquets...")
t0 = time.time()
files_2026 = sorted(DATA_DIR.glob("avgo_cbbo_2026*.parquet"))
log(f"  {len(files_2026)} files")

expiry_index = defaultdict(list)
strikes_by_day = defaultdict(lambda: defaultdict(set))

for i, f in enumerate(files_2026):
    df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
    df["sym_clean"] = df["symbol"].str.strip()
    extracted = df["sym_clean"].str.extract(SYM_RE)
    extracted.columns = ["root", "yymmdd", "right", "strike_str"]
    df["expiry"] = pd.to_datetime(extracted["yymmdd"], format="%y%m%d").dt.date
    df["right"] = extracted["right"]
    df["strike"] = extracted["strike_str"].astype(float) / 1000
    df = df.dropna(subset=["expiry", "right", "strike"])

    # Trade date from filename
    ds = f.stem.replace("avgo_cbbo_", "")
    td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))

    for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
        expiry_index[(exp, strike, right)].append(grp[["bid_px_00", "ask_px_00"]])
        strikes_by_day[td][exp].add(strike)

log(f"  loaded ({time.time()-t0:.0f}s)")

log("Consolidating...")
for k in list(expiry_index.keys()):
    expiry_index[k] = pd.concat(expiry_index[k]).sort_index()
log(f"  consolidated ({len(expiry_index)} keys, {time.time()-t0:.0f}s)")

trade_dates = sorted(strikes_by_day.keys())
log(f"  trade dates: {len(trade_dates)} ({trade_dates[0]} → {trade_dates[-1]})")


# ─── AVGO spot ──────────────────────────────────────────────────────────────
spot = yf.Ticker("AVGO").history(start="2026-02-01", end="2026-06-01")
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}


# ─── IC simulator (same as v3) ──────────────────────────────────────────────
def find_strike(avail, target, spacing=2.5):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d in [2.5, 5, 7.5, 10, 12.5]:
        for sign in [+1, -1]:
            t = snapped + sign * d
            if t in avail: return t
    return None


def at_or_after(df, ts):
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate(td, target_exp, otm_pct, wing, spot_entry, spot_exp_close, entry_ts, exit_ts):
    avail = strikes_by_day[td].get(target_exp, set())
    if not avail: return None
    sp_k = find_strike(avail, spot_entry * (1 - otm_pct))
    sc_k = find_strike(avail, spot_entry * (1 + otm_pct))
    if sp_k is None or sc_k is None: return None
    lp_k = find_strike(avail, sp_k - wing)
    lc_k = find_strike(avail, sc_k + wing)
    if lp_k is None or lc_k is None: return None

    sp_df = expiry_index.get((target_exp, sp_k, "P"))
    lp_df = expiry_index.get((target_exp, lp_k, "P"))
    sc_df = expiry_index.get((target_exp, sc_k, "C"))
    lc_df = expiry_index.get((target_exp, lc_k, "C"))
    if any(x is None for x in [sp_df, lp_df, sc_df, lc_df]):
        return None

    e_sp = at_or_after(sp_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts)
    e_sc = at_or_after(sc_df, entry_ts)
    e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_lp, e_sc, e_lc]):
        return None

    credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
              - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"]))
    if credit < 0.15: return None

    pw = sp_k - lp_k; cw = lc_k - sc_k
    max_w = max(pw, cw)
    max_loss = (max_w - credit) * 100
    stop_debit = credit * STOP_MULT
    pt_debit = credit * (1 - PT_FRAC)

    win_sp = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    win_lp = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    win_sc = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    win_lc = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    exit_val = None; exit_reason = None
    if not any(s.empty for s in [win_sp, win_lp, win_sc, win_lc]):
        sp_r = win_sp.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
        lp_r = win_lp.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
        sc_r = win_sc.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
        lc_r = win_lc.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
        al = sp_r.join(lp_r, how="inner").join(sc_r, how="inner").join(lc_r, how="inner")
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
        sp_int = max(0, sp_k - spot_exp_close)
        lp_int = max(0, lp_k - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc_k)
        lc_int = max(0, spot_exp_close - lc_k)
        exit_val = sp_int - lp_int + sc_int - lc_int
        exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {
        "trade_date": td, "dow": td.strftime("%a"),
        "expiry": target_exp, "dte": (target_exp - td).days,
        "otm_pct": otm_pct * 100, "wing": wing,
        "sp_k": sp_k, "lp_k": lp_k, "sc_k": sc_k, "lc_k": lc_k,
        "credit": credit, "credit_dollars": credit * 100,
        "max_loss_dollars": max_loss,
        "exit_reason": exit_reason, "exit_value": exit_val,
        "pnl": pnl,
    }


# ─── Sweep ──────────────────────────────────────────────────────────────────
OTM_PCTS = [0.01, 0.02, 0.03, 0.05]
WIDTHS = [5, 10, 15, 25]
DTE_BUCKETS = [(0, 0, "0DTE"), (1, 3, "1-3DTE"), (4, 7, "4-7DTE"), (8, 14, "8-14DTE")]

log("\nRunning sweep on 2026 data...")
results = []
t4 = time.time()

for td in trade_dates:
    if td not in spot_by_date: continue
    spot_row = spot_by_date[td]
    spot_open = float(spot_row["Open"])
    spot_close = float(spot_row["Close"])
    spot_entry = spot_open + 0.3 * (spot_close - spot_open)
    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=ENTRY_HOUR_ET, minute=ENTRY_MIN_ET).astimezone(UTC))

    available_exps = sorted(strikes_by_day[td].keys())

    for otm in OTM_PCTS:
        for w in WIDTHS:
            for lo, hi, dte_label in DTE_BUCKETS:
                target_exp = None
                for exp in available_exps:
                    dte = (exp - td).days
                    if lo <= dte <= hi:
                        target_exp = exp; break
                if target_exp is None: continue
                if target_exp not in spot_by_date: continue
                spot_exp_close = float(spot_by_date[target_exp]["Close"])
                exit_ts = pd.Timestamp(datetime.combine(target_exp, datetime.min.time(), tzinfo=ET).replace(
                    hour=16, minute=0).astimezone(UTC))

                r = simulate(td, target_exp, otm, w, spot_entry, spot_exp_close, entry_ts, exit_ts)
                if r is not None:
                    r["dte_bucket"] = dte_label
                    results.append(r)

log(f"Done: {time.time()-t4:.0f}s, {len(results)} sims")

df = pd.DataFrame(results)
df.to_csv("/tmp/avgo_ic_2026_results.csv", index=False)

# ─── Report ─────────────────────────────────────────────────────────────────
log("\n" + "=" * 120)
log("AVGO IC MARCH-MAY 2026 — Variant sweep (min 5 trades)")
log("=" * 120)
log(f"{'OTM%':<6}{'Wing':<6}{'DTE':<10}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}")
log("-" * 110)
rows = []
for otm in [1.0, 2.0, 3.0, 5.0]:
    for w in [5, 10, 15, 25]:
        for dte_label in ["0DTE", "1-3DTE", "4-7DTE", "8-14DTE"]:
            sub = df[(df["otm_pct"] == otm) & (df["wing"] == w) & (df["dte_bucket"] == dte_label)]
            if len(sub) < 5: continue
            n = len(sub)
            wr = (sub["pnl"] > 0).mean() * 100
            avg_cr = sub["credit_dollars"].mean()
            total = sub["pnl"].sum()
            avg = sub["pnl"].mean()
            best = sub["pnl"].max()
            worst = sub["pnl"].min()
            log(f"{otm:<5.0f}%{w:<6}{dte_label:<10}{n:<5}{wr:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+9.0f}${best:<+8.0f}${worst:<+9.0f}")
            rows.append({"otm_pct": otm, "wing": w, "dte": dte_label, "n": n, "win_rate": wr,
                         "avg_credit": avg_cr, "total_pnl": total, "avg_pnl": avg, "best": best, "worst": worst})

# ─── KEY: 0DTE by day-of-week ────────────────────────────────────────────────
log("\n" + "=" * 120)
log("⭐ 0DTE BY DAY-OF-WEEK — does the new Mon/Wed structure work like Friday?")
log("=" * 120)

log(f"\n{'OTM%':<6}{'Wing':<6}{'Day':<6}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}")
log("-" * 100)
for otm in [1.0, 2.0, 3.0, 5.0]:
    for w in [10, 15, 25]:
        for dow in ["Mon", "Wed", "Fri"]:
            sub = df[(df["otm_pct"] == otm) & (df["wing"] == w) & (df["dte_bucket"] == "0DTE") & (df["dow"] == dow)]
            if len(sub) < 3: continue
            n = len(sub)
            wr = (sub["pnl"] > 0).mean() * 100
            avg_cr = sub["credit_dollars"].mean()
            total = sub["pnl"].sum()
            avg = sub["pnl"].mean()
            best = sub["pnl"].max()
            worst = sub["pnl"].min()
            log(f"{otm:<5.0f}%{w:<6}{dow:<6}{n:<5}{wr:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+9.0f}${best:<+8.0f}${worst:<+9.0f}")
        log("")  # blank line between OTM/wing combos

# ─── Combined M/W/F 0DTE — your hypothetical "$100/wk" strategy ─────────────
log("\n" + "=" * 120)
log("⭐ COMBINED M+W+F 0DTE TOTAL (extrapolated to annual)")
log("=" * 120)
log(f"\n{'OTM%':<6}{'Wing':<6}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Per Wk':<10}{'Annual (est)':<15}")
log("-" * 110)
for otm in [1.0, 2.0, 3.0, 5.0]:
    for w in [10, 15, 25]:
        sub = df[(df["otm_pct"] == otm) & (df["wing"] == w) & (df["dte_bucket"] == "0DTE") & (df["dow"].isin(["Mon", "Wed", "Fri"]))]
        if len(sub) < 10: continue
        n = len(sub)
        wr = (sub["pnl"] > 0).mean() * 100
        avg_cr = sub["credit_dollars"].mean()
        total = sub["pnl"].sum()
        avg = sub["pnl"].mean()
        weeks_covered = 12  # March-May 2026 = ~12 weeks
        per_wk = total / weeks_covered
        annual = per_wk * 52
        log(f"{otm:<5.0f}%{w:<6}{n:<5}{wr:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+9.0f}${per_wk:<+9.0f}${annual:<+13,.0f}")

summary = pd.DataFrame(rows)
summary.to_csv("/tmp/avgo_ic_2026_summary.csv", index=False)
log("\n→ /tmp/avgo_ic_2026_results.csv, /tmp/avgo_ic_2026_summary.csv saved")
