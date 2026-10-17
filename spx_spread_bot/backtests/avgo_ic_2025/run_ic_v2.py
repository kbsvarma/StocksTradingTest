"""AVGO IC backtest v2 — efficient version.

Load all parquets ONCE into memory. Index by (expiry, strike, right).
Then sweep variants without re-loading.
"""
import re
import time
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


def parse_sym(s):
    m = SYM_RE.match(s.strip())
    if not m: return None, None, None
    _, yymmdd, right, strike_str = m.groups()
    return datetime.strptime(yymmdd, "%y%m%d").date(), right, int(strike_str) / 1000


# ─── Step 1: Load all data once ──────────────────────────────────────────────
print("Loading all parquets...")
t0 = time.time()
files = sorted(DATA_DIR.glob("avgo_cbbo_*.parquet"))
print(f"  {len(files)} files")

# Build a single big dataframe; parse symbols once
frames = []
for f in files:
    df = pd.read_parquet(f)
    # Keep only minute bars in regular trading hours: 14:30-20:00 UTC
    df = df[["bid_px_00", "ask_px_00", "symbol"]]
    frames.append(df)
all_df = pd.concat(frames)
print(f"  Total rows: {len(all_df):,}  ({time.time()-t0:.0f}s)")

# Parse symbols (vectorized)
t1 = time.time()
parsed = all_df["symbol"].str.strip().str.extract(SYM_RE)
parsed.columns = ["root", "yymmdd", "right", "strike_str"]
all_df["expiry"] = pd.to_datetime(parsed["yymmdd"], format="%y%m%d").dt.date
all_df["right"] = parsed["right"]
all_df["strike"] = parsed["strike_str"].astype(int) / 1000
all_df = all_df.dropna(subset=["expiry", "right", "strike"])
print(f"  Parsed symbols ({time.time()-t1:.0f}s)")

# Build index for fast lookup: (date, expiry, strike, right) → dataframe of quotes by minute
# We use a multi-level index for fast slicing
t2 = time.time()
all_df["trade_date"] = pd.to_datetime(all_df.index).tz_convert(UTC).date
all_df = all_df.sort_index()
print(f"  Indexed ({time.time()-t2:.0f}s, total {time.time()-t0:.0f}s)")
print(f"  Memory: ~{all_df.memory_usage(deep=True).sum() / 1e9:.2f} GB")

# Pre-aggregate: group by trade_date for quick per-day access
print("Building per-day cache...")
t3 = time.time()
day_groups = {td: g for td, g in all_df.groupby("trade_date")}
trade_dates = sorted(day_groups.keys())
print(f"  {len(trade_dates)} unique trade dates ({time.time()-t3:.0f}s)")
print(f"  Range: {trade_dates[0]} → {trade_dates[-1]}")


# ─── Step 2: AVGO spot ──────────────────────────────────────────────────────
print("Fetching AVGO spot...")
spot = yf.Ticker("AVGO").history(start="2025-01-01", end="2026-01-15")
spot.index = pd.to_datetime(spot.index).tz_localize(None)
spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot.iterrows()}


# ─── Step 3: IC simulation ───────────────────────────────────────────────────
def find_strike_in(available_strikes, target, spacing=2.5):
    """Find closest strike, snap to spacing."""
    snapped = round(target / spacing) * spacing
    if snapped in available_strikes:
        return snapped
    for delta in [2.5, 5, 7.5, 10, 12.5]:
        for sign in [+1, -1]:
            t = snapped + sign * delta
            if t in available_strikes:
                return t
    return None


def simulate_ic(td, day_data, multi_day_data, otm_pct, wing, target_expiry, spot_entry, spot_exp_close, entry_ts, exit_ts):
    # Filter to chosen expiry from THIS day's data
    exp_df = day_data[day_data["expiry"] == target_expiry]
    if exp_df.empty:
        return {"skip": "no expiry data this day"}

    available = set(exp_df["strike"].unique())

    sp_target = spot_entry * (1 - otm_pct)
    sc_target = spot_entry * (1 + otm_pct)
    sp_k = find_strike_in(available, sp_target)
    sc_k = find_strike_in(available, sc_target)
    if sp_k is None or sc_k is None:
        return {"skip": "no short strikes"}

    lp_k = find_strike_in(available, sp_k - wing)
    lc_k = find_strike_in(available, sc_k + wing)
    if lp_k is None or lc_k is None:
        return {"skip": "no wing strikes"}

    # Get entry quotes from THIS day's data at entry_ts
    def get_quote_at(df, exp, strike, right, ts):
        sub = df[(df["expiry"] == exp) & (df["strike"] == strike) & (df["right"] == right)]
        sub = sub[sub.index >= ts]
        if sub.empty: return None
        return sub.iloc[0]

    e_sp = get_quote_at(exp_df, target_expiry, sp_k, "P", entry_ts)
    e_lp = get_quote_at(exp_df, target_expiry, lp_k, "P", entry_ts)
    e_sc = get_quote_at(exp_df, target_expiry, sc_k, "C", entry_ts)
    e_lc = get_quote_at(exp_df, target_expiry, lc_k, "C", entry_ts)
    if any(x is None for x in [e_sp, e_lp, e_sc, e_lc]):
        return {"skip": "no entry quotes"}

    credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
              - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"]))
    if credit < 0.15:
        return {"skip": f"thin credit {credit:.2f}"}

    actual_pw = sp_k - lp_k
    actual_cw = lc_k - sc_k
    max_w = max(actual_pw, actual_cw)
    max_loss_dollars = (max_w - credit) * 100

    stop_debit = credit * STOP_MULT
    pt_debit = credit * (1 - PT_FRAC)

    # Walk minute by minute from entry_ts to exit_ts using ALL data
    # (multi_day_data covers the full period to expiry for multi-day cases)
    def get_leg_series(df, exp, strike, right):
        sub = df[(df["expiry"] == exp) & (df["strike"] == strike) & (df["right"] == right)]
        return sub[["bid_px_00", "ask_px_00"]]

    sp_s = get_leg_series(multi_day_data, target_expiry, sp_k, "P")
    lp_s = get_leg_series(multi_day_data, target_expiry, lp_k, "P")
    sc_s = get_leg_series(multi_day_data, target_expiry, sc_k, "C")
    lc_s = get_leg_series(multi_day_data, target_expiry, lc_k, "C")
    if any(s.empty for s in [sp_s, lp_s, sc_s, lc_s]):
        # Settle at expiry without intraday scan
        sp_int = max(0, sp_k - spot_exp_close)
        lp_int = max(0, lp_k - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc_k)
        lc_int = max(0, spot_exp_close - lc_k)
        exit_val = sp_int - lp_int + sc_int - lc_int
        pnl = (credit - exit_val) * 100
        return {"trade_date": td, "expiry": target_expiry, "dte": (target_expiry - td).days,
                "otm_pct": otm_pct*100, "wing": wing,
                "sp_k": sp_k, "lp_k": lp_k, "sc_k": sc_k, "lc_k": lc_k,
                "credit": credit, "credit_dollars": credit*100, "max_loss_dollars": max_loss_dollars,
                "exit_reason": "expiry_no_intraday", "exit_value": exit_val,
                "pnl": pnl, "win": pnl > 0}

    # Align minute bars
    sp_s = sp_s.rename(columns={"bid_px_00": "bid_sp", "ask_px_00": "ask_sp"})
    lp_s = lp_s.rename(columns={"bid_px_00": "bid_lp", "ask_px_00": "ask_lp"})
    sc_s = sc_s.rename(columns={"bid_px_00": "bid_sc", "ask_px_00": "ask_sc"})
    lc_s = lc_s.rename(columns={"bid_px_00": "bid_lc", "ask_px_00": "ask_lc"})
    aligned = sp_s.join(lp_s, how="inner").join(sc_s, how="inner").join(lc_s, how="inner")
    aligned = aligned[(aligned.index >= entry_ts) & (aligned.index <= exit_ts)]
    if aligned.empty:
        # Fall through to expiry settlement
        sp_int = max(0, sp_k - spot_exp_close)
        lp_int = max(0, lp_k - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc_k)
        lc_int = max(0, spot_exp_close - lc_k)
        exit_val = sp_int - lp_int + sc_int - lc_int
        pnl = (credit - exit_val) * 100
        return {"trade_date": td, "expiry": target_expiry, "dte": (target_expiry - td).days,
                "otm_pct": otm_pct*100, "wing": wing,
                "sp_k": sp_k, "lp_k": lp_k, "sc_k": sc_k, "lc_k": lc_k,
                "credit": credit, "credit_dollars": credit*100, "max_loss_dollars": max_loss_dollars,
                "exit_reason": "expiry_no_align", "exit_value": exit_val,
                "pnl": pnl, "win": pnl > 0}

    exit_val = None; exit_reason = None
    # Compute debit-to-close at each minute
    aligned["debit"] = aligned["ask_sp"] + aligned["ask_sc"] - aligned["bid_lp"] - aligned["bid_lc"]
    # Find first SL or PT hit
    sl_hits = aligned[aligned["debit"] >= stop_debit]
    pt_hits = aligned[aligned["debit"] <= pt_debit]
    sl_ts = sl_hits.index.min() if not sl_hits.empty else None
    pt_ts = pt_hits.index.min() if not pt_hits.empty else None
    first_event = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        first_event = ("stop_loss", sl_ts, aligned.loc[sl_ts, "debit"])
    elif pt_ts is not None:
        first_event = ("profit_target", pt_ts, aligned.loc[pt_ts, "debit"])

    if first_event:
        exit_reason, _, exit_val = first_event
    else:
        # Settle at expiry against spot close
        sp_int = max(0, sp_k - spot_exp_close)
        lp_int = max(0, lp_k - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc_k)
        lc_int = max(0, spot_exp_close - lc_k)
        exit_val = sp_int - lp_int + sc_int - lc_int
        exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {"trade_date": td, "expiry": target_expiry, "dte": (target_expiry - td).days,
            "otm_pct": otm_pct*100, "wing": wing,
            "sp_k": sp_k, "lp_k": lp_k, "sc_k": sc_k, "lc_k": lc_k,
            "credit": credit, "credit_dollars": credit*100, "max_loss_dollars": max_loss_dollars,
            "exit_reason": exit_reason, "exit_value": exit_val,
            "pnl": pnl, "win": pnl > 0}


# ─── Step 4: Sweep ───────────────────────────────────────────────────────────
OTM_PCTS = [0.01, 0.02, 0.03, 0.05]
WIDTHS = [5, 10, 15, 25]
DTE_BUCKETS = [(0, 0, "0DTE"), (1, 3, "1-3DTE"), (4, 7, "4-7DTE"), (8, 14, "8-14DTE")]

results = []
t4 = time.time()

for i, td in enumerate(trade_dates):
    day_data = day_groups[td]
    if td not in spot_by_date:
        continue
    spot_row = spot_by_date[td]
    spot_open = float(spot_row["Open"])
    spot_close = float(spot_row["Close"])
    spot_entry = spot_open + 0.3 * (spot_close - spot_open)  # 10:30 proxy

    entry_ts = datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=ENTRY_HOUR_ET, minute=ENTRY_MIN_ET).astimezone(UTC)

    available_exps = sorted(day_data["expiry"].unique())

    for otm in OTM_PCTS:
        for w in WIDTHS:
            for dte_lo, dte_hi, dte_label in DTE_BUCKETS:
                target_exp = None
                for exp in available_exps:
                    dte = (exp - td).days
                    if dte_lo <= dte <= dte_hi:
                        target_exp = exp
                        break
                if target_exp is None:
                    continue
                if target_exp not in spot_by_date:
                    continue
                spot_exp_close = float(spot_by_date[target_exp]["Close"])

                # Exit deadline = expiry 16:00 ET = 20:00 UTC
                exit_ts = datetime.combine(target_exp, datetime.min.time(), tzinfo=ET).replace(
                    hour=16, minute=0).astimezone(UTC)

                # For multi-day: slice all_df for the expiry leg trajectory
                if dte_hi == 0:
                    multi_data = day_data  # 0DTE only needs today
                else:
                    # Slice all_df between td and target_exp for this expiry
                    mask = (all_df["trade_date"] >= td) & (all_df["trade_date"] <= target_exp) & (all_df["expiry"] == target_exp)
                    multi_data = all_df[mask]

                r = simulate_ic(td, day_data, multi_data, otm, w, target_exp,
                                spot_entry, spot_exp_close, entry_ts, exit_ts)
                r["dte_bucket"] = dte_label
                results.append(r)

    if (i + 1) % 20 == 0:
        elapsed = time.time() - t4
        print(f"  {i+1}/{len(trade_dates)} days, {len(results)} sims, {elapsed:.0f}s")

print(f"\nDone simulating: {time.time()-t4:.0f}s")

df = pd.DataFrame(results)
df.to_csv("/tmp/avgo_ic_v2_results.csv", index=False)

traded = df[df["pnl"].notna()].copy()
print(f"\nTotal sims: {len(df)}, valid trades: {len(traded)}, skipped: {df['skip'].notna().sum() if 'skip' in df.columns else 0}")

# === Summary by variant ===
print("\n" + "=" * 120)
print("AVGO IC SWEEP 2025 — variants with ≥10 trades")
print("=" * 120)
print(f"{'OTM%':<6}{'Wing':<6}{'DTE':<10}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<9}{'Worst':<10}{'Stops':<7}{'PT':<6}{'Exp':<6}")
print("-" * 120)
rows = []
for otm in [1.0, 2.0, 3.0, 5.0]:
    for w in [5, 10, 15, 25]:
        for dte_label in ["0DTE", "1-3DTE", "4-7DTE", "8-14DTE"]:
            sub = traded[(traded["otm_pct"] == otm) & (traded["wing"] == w) & (traded["dte_bucket"] == dte_label)]
            if len(sub) < 10:
                continue
            n = len(sub)
            wr = (sub["pnl"] > 0).mean() * 100
            avg_cr = sub["credit_dollars"].mean()
            total = sub["pnl"].sum()
            avg = sub["pnl"].mean()
            best = sub["pnl"].max()
            worst = sub["pnl"].min()
            stops = (sub["exit_reason"] == "stop_loss").sum()
            pts = (sub["exit_reason"] == "profit_target").sum()
            exps = (sub["exit_reason"].isin(["expiry", "expiry_no_intraday", "expiry_no_align"])).sum()
            print(f"{otm:<5.0f}%{w:<6}{dte_label:<10}{n:<5}{wr:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+9.0f}${best:<+8.0f}${worst:<+9.0f}{stops:<7}{pts:<6}{exps:<6}")
            rows.append({"otm_pct": otm, "wing": w, "dte": dte_label, "n": n, "win_rate": wr,
                         "avg_credit": avg_cr, "total_pnl": total, "avg_pnl": avg,
                         "best": best, "worst": worst, "stops": stops, "pts": pts, "exps": exps})

summary = pd.DataFrame(rows)
summary.to_csv("/tmp/avgo_ic_v2_summary.csv", index=False)

print("\n" + "=" * 120)
print("TOP 10 BY TOTAL P&L")
print("=" * 120)
top = summary.sort_values("total_pnl", ascending=False).head(10)
print(top.to_string(index=False))

print("\n" + "=" * 120)
print("BOTTOM 5 BY TOTAL P&L")
print("=" * 120)
bot = summary.sort_values("total_pnl").head(5)
print(bot.to_string(index=False))

print("\n" + "=" * 120)
print("TOP 10 BY AVG P&L PER TRADE (filtered N>=30)")
print("=" * 120)
top_avg = summary[summary["n"] >= 30].sort_values("avg_pnl", ascending=False).head(10)
print(top_avg.to_string(index=False))
