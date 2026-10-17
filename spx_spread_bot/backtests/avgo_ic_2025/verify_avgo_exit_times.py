"""Test multiple exit times with STRICT simulation.

If 15:45 quotes have data gaps, earlier closes might:
1. Have better quote data (more reliable bid/ask)
2. Still capture most theta decay
3. Avoid late-day liquidity issues

Test exit times: 14:00, 14:30, 14:45, 15:00, 15:15, 15:30, 15:45
For each: % valid trades, win rate, total P&L with realistic slippage.
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")
SPACING = 2.5

def log(m): print(m, flush=True)

log("Loading AVGO 2026 data...")
t0 = time.time()
expiry_index = {}
strikes_by_day = defaultdict(lambda: defaultdict(set))
for f in sorted(DATA.glob("avgo_cbbo_2026*.parquet")):
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
log(f"Loaded ({time.time()-t0:.0f}s)")

spot_hist = yf.Ticker("AVGO").history(start="2026-01-01", end="2026-06-01", auto_adjust=False)
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


def at_or_before(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index <= ts]
    return None if sub.empty else sub.iloc[-1]


def find_quote_near(df, ts, window_minutes=5):
    """Find quote within ±window minutes of target ts."""
    if df is None or df.empty: return None
    window = pd.Timedelta(minutes=window_minutes)
    sub = df.loc[(df.index >= ts - window) & (df.index <= ts + window)]
    if sub.empty: return None
    # Pick the closest to target ts
    deltas = (sub.index - ts).total_seconds().abs()
    idx = deltas.argmin()
    return sub.iloc[idx]


def simulate_ic_strict(td, otm_pct, wing_pts, exit_hour, exit_min,
                       slippage_per_leg=0.0, use_quote_window=True):
    """Simulate with configurable exit time. Uses ±5min window for exit quotes."""
    if td not in spot_by_date: return None
    if td not in strikes_by_day[td]: return None
    spot_row = spot_by_date[td]
    spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
    spot_entry = spot_o + 0.3 * (spot_c - spot_o)

    avail = strikes_by_day[td][td]
    sp = find_strike(avail, spot_entry * (1 - otm_pct))
    sc = find_strike(avail, spot_entry * (1 + otm_pct))
    if sp is None or sc is None: return None
    lp = find_strike(avail, sp - wing_pts)
    lc = find_strike(avail, sc + wing_pts)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, td, sp, "P"))
    sc_df = expiry_index.get((td, td, sc, "C"))
    lp_df = expiry_index.get((td, td, lp, "P"))
    lc_df = expiry_index.get((td, td, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=exit_hour, minute=exit_min).astimezone(UTC))

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]):
        return {"status": "no_entry"}

    credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
              - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
              - 4 * slippage_per_leg)
    if credit < 0.05:
        return {"status": "thin_credit"}

    stop_debit = credit * 2.0
    pt_debit = credit * 0.5

    # Walk for SL/PT between entry and exit
    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]
    if any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]):
        return {"status": "no_intraday"}

    sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
    sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
    lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
    lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
    al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
    if al.empty:
        return {"status": "no_aligned"}

    al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
    sl = al[al["debit"] >= stop_debit]; pt = al[al["debit"] <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None
    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = al.loc[sl_ts, "debit"] + 4 * slippage_per_leg
        exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = al.loc[pt_ts, "debit"] + 4 * slippage_per_leg
        exit_reason = "profit_target"
    else:
        # Use the LAST debit in the walk window (closest to exit_ts)
        exit_val = al.iloc[-1]["debit"] + 4 * slippage_per_leg
        exit_reason = "forced_close"

    if pd.isna(exit_val):
        return {"status": "nan_exit"}

    pnl = (credit - exit_val) * 100
    return {
        "status": "ok",
        "trade_date": td, "dow": td.strftime("%a"),
        "credit": credit, "credit_dollars": credit * 100,
        "exit_val": exit_val, "exit_reason": exit_reason,
        "pnl": pnl,
    }


def run_all(exit_hour, exit_min, slippage=0.0):
    results = []
    statuses = defaultdict(int)
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue
        r = simulate_ic_strict(td, 0.005, 25, exit_hour, exit_min, slippage)
        if r:
            statuses[r["status"]] += 1
            if r["status"] == "ok":
                results.append(r)
    return pd.DataFrame(results), dict(statuses)


log("\n" + "=" * 110)
log("AVGO IC 0.5%/25pt — EXIT TIME SWEEP with strict real-quote simulation")
log("=" * 110)
log(f"{'Exit':<8}{'Slippage':<10}{'Valid':<8}{'OK%':<6}{'Win%':<7}{'Total':<11}{'AvgP&L':<9}{'Worst':<9}{'NaN%':<7}")
log("-" * 90)

for hour, minute in [(14, 0), (14, 30), (14, 45), (15, 0), (15, 15), (15, 30), (15, 45)]:
    for slip in [0.0, 0.05, 0.10]:
        df, statuses = run_all(hour, minute, slip)
        total_attempts = sum(statuses.values())
        valid_pct = statuses.get("ok", 0) / total_attempts * 100 if total_attempts else 0
        nan_pct = statuses.get("nan_exit", 0) / total_attempts * 100 if total_attempts else 0
        if df.empty:
            log(f"{hour:02d}:{minute:02d}    ${slip:<8.2f}{0:<8}{valid_pct:<5.0f}%--      $--          --        --        {nan_pct:<6.0f}%")
            continue
        n = len(df); wins = (df["pnl"] > 0).sum()
        log(f"{hour:02d}:{minute:02d}    ${slip:<8.2f}{n:<8}{valid_pct:<5.0f}%{wins/n*100:<6.0f}%${df['pnl'].sum():<+10,.0f}${df['pnl'].mean():<+8.0f}${df['pnl'].min():<+8.0f}{nan_pct:<6.0f}%")
    log("")  # blank between exit times

# Best variant detail
log("\n" + "=" * 110)
log("BEST EXIT TIME — find the one with most valid trades AND positive EV")
log("=" * 110)

best_results = []
for hour, minute in [(14, 0), (14, 30), (14, 45), (15, 0), (15, 15), (15, 30), (15, 45)]:
    df, statuses = run_all(hour, minute, 0.05)  # use realistic slippage
    if df.empty: continue
    n = len(df); wins = (df["pnl"] > 0).sum()
    total = df["pnl"].sum()
    best_results.append({
        "exit": f"{hour:02d}:{minute:02d}",
        "valid_pct": statuses.get("ok", 0) / sum(statuses.values()) * 100,
        "n": n, "win_rate": wins/n*100,
        "total": total, "avg": df["pnl"].mean(),
        "worst": df["pnl"].min(),
    })

best_df = pd.DataFrame(best_results)
log(f"\n(Using $0.05/leg slippage = $0.20 round-trip)")
log(f"{'Exit':<8}{'Valid%':<9}{'N':<5}{'Win%':<7}{'Total':<11}{'AvgP&L':<10}{'Worst':<9}")
log("-" * 75)
for _, r in best_df.iterrows():
    log(f"{r['exit']:<8}{r['valid_pct']:<8.0f}%{r['n']:<5}{r['win_rate']:<6.0f}%${r['total']:<+10,.0f}${r['avg']:<+9.0f}${r['worst']:<+8.0f}")

# Per-week annualized
log(f"\nAnnualized estimates (12-week sample × 4.33 to year):")
for _, r in best_df.iterrows():
    annual = r["total"] / 12 * 52
    log(f"  {r['exit']}: ${annual:+,.0f}/year")
