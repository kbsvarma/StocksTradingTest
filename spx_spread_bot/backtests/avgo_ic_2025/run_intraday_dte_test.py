"""Test intraday IC across DTE buckets on AVGO 2025 (Friday-only weeklies).

Setup:
- Each trading day Mon-Fri, enter IC on the upcoming Friday expiry
- 09:45 ET entry, 15:45 ET forced close (intraday only, no overnight)
- 0.5% OTM, 25pt wings, 2.0x SL, 50% PT
- Track results per DTE: 0 (Fri), 1 (Thu), 2 (Wed), 3 (Tue), 4 (Mon)

The 2025 data has 248 parquets — 50 Fridays × ~5 days = 250 potential trades.
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


# ─── Load 2025 data only ───────────────────────────────────────────────────
log("Loading 2025 parquets...")
t0 = time.time()
expiry_index = defaultdict(list)
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA_DIR.glob("avgo_cbbo_2025*.parquet")):
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
        expiry_index[(td, exp, strike, right)] = grp[["bid_px_00", "ask_px_00"]]
        strikes_by_day[td][exp].add(strike)

log(f"Loaded ({time.time()-t0:.0f}s, {len(strikes_by_day)} trade dates)")

# AVGO spot
spot = yf.Ticker("AVGO").history(start="2025-01-01", end="2026-02-01", auto_adjust=False)
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}


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


def simulate_intraday(td, target_exp, otm, wing, stop_mult=2.0, pt_frac=0.5):
    """Enter at 09:45, close at 15:45 SAME DAY (no overnight)."""
    if td not in spot_by_date: return None
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    # Use open + 0.3 * (close - open) as 09:45 spot proxy
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td].get(target_exp, set())
    if not avail: return None

    sp = find_strike(avail, spot_entry * (1 - otm))
    sc = find_strike(avail, spot_entry * (1 + otm))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing)
    lc = find_strike(avail, sc + wing)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, target_exp, sp, "P"))
    sc_df = expiry_index.get((td, target_exp, sc, "C"))
    lp_df = expiry_index.get((td, target_exp, lp, "P"))
    lc_df = expiry_index.get((td, target_exp, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=15, minute=45).astimezone(UTC))

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]): return None

    credit = float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"]) - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
    if credit < 0.15: return None

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    if any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]):
        return None

    sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
    sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
    lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
    lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
    al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
    if al.empty: return None

    al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
    sl = al[al["debit"] >= stop_debit]
    pt = al[al["debit"] <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = al.loc[sl_ts, "debit"]; exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = al.loc[pt_ts, "debit"]; exit_reason = "profit_target"
    else:
        # Forced close at 15:45
        exit_val = al.iloc[-1]["debit"]
        exit_reason = "forced_close"

    pnl = (credit - exit_val) * 100
    dte = (target_exp - td).days
    return {
        "trade_date": td, "dow": td.strftime("%a"),
        "expiry": target_exp, "dte": dte,
        "credit": credit, "credit_dollars": credit * 100,
        "exit_reason": exit_reason, "exit_val": exit_val,
        "pnl": pnl,
    }


# ─── Run intraday backtest across all trade days ───────────────────────────
log("\nRunning intraday IC across 2025 (close before EOD same day)...")

trades = []
for td in sorted(strikes_by_day.keys()):
    # Find the next Friday expiry available
    available_exps = sorted(strikes_by_day[td].keys())
    target_exp = None
    for exp in available_exps:
        if exp >= td and exp.weekday() == 4:  # Friday
            dte = (exp - td).days
            if 0 <= dte <= 4:  # Same week's Friday
                target_exp = exp
                break
    if target_exp is None: continue

    r = simulate_intraday(td, target_exp, otm=0.005, wing=25)
    if r: trades.append(r)

df = pd.DataFrame(trades)
log(f"Total trades: {len(df)}")

# ─── Summary by DTE ────────────────────────────────────────────────────────
log("\n" + "=" * 110)
log("RESULTS BY DTE (same-day enter and exit, 09:45 → 15:45 ET)")
log("=" * 110)
log(f"{'DTE':<5}{'DOW':<6}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}{'Stops':<7}{'PT':<5}")
log("-" * 100)

for dte in [0, 1, 2, 3, 4]:
    sub = df[df["dte"] == dte]
    if sub.empty: continue
    n = len(sub)
    wins = (sub["pnl"] > 0).sum()
    avg_cr = sub["credit_dollars"].mean()
    total = sub["pnl"].sum()
    avg = sub["pnl"].mean()
    best = sub["pnl"].max()
    worst = sub["pnl"].min()
    stops = (sub["exit_reason"] == "stop_loss").sum()
    pts = (sub["exit_reason"] == "profit_target").sum()
    dow = sub["dow"].iloc[0]
    log(f"{dte:<5}{dow:<6}{n:<5}{wins/n*100:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+9.0f}${best:<+8.0f}${worst:<+9.0f}{stops:<7}{pts:<5}")

# Total combined
log(f"\nCOMBINED ALL DTEs:")
total_pnl = df["pnl"].sum()
wins = (df["pnl"] > 0).sum()
log(f"  N={len(df)}, Win={wins/len(df)*100:.0f}%, Total=${total_pnl:+,.0f}, Avg=${df['pnl'].mean():+.0f}")
weeks = 50  # 2025 had ~50 Fridays
log(f"  Per week: ${total_pnl/weeks:+.0f}")
log(f"  Annual extrapolation: ${total_pnl:+,.0f} (this IS the year)")

# Distribution by exit reason
log(f"\nExit reasons:")
for er, cnt in df["exit_reason"].value_counts().items():
    sub = df[df["exit_reason"] == er]
    log(f"  {er}: {cnt} ({cnt/len(df)*100:.0f}%), avg ${sub['pnl'].mean():+.0f}")

# Distribution by day of week
log(f"\nBy day of week:")
for dow in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
    sub = df[df["dow"] == dow]
    if sub.empty: continue
    n = len(sub); wins = (sub["pnl"] > 0).sum()
    log(f"  {dow}: N={n}, Win={wins/n*100:.0f}%, Total=${sub['pnl'].sum():+,.0f}, Avg=${sub['pnl'].mean():+.0f}")

# Save
df.to_csv("/tmp/avgo_intraday_dte_results.csv", index=False)
log("\nResults saved.")
