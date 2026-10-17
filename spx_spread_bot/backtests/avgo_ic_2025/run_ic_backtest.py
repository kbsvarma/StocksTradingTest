"""AVGO Iron Condor backtest on real DataBento 2025 cache.

Sweep across:
  - OTM%: 1, 2, 3, 5
  - Wing width: 5, 10, 15, 25 pt
  - DTE buckets: 0, 1-3, 4-7, 8-14
  - Entry at 10:30 ET (one trade per day per variant)
  - Exit: 50% profit target, 2× stop loss, or hold to expiry

Settlement: at expiry close (4 PM ET), use AVGO spot from yfinance daily close.
Intraday SL/PT: minute-by-minute scan from entry to expiry.
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


# ─── Symbol helpers ─────────────────────────────────────────────────────────
def parse_sym(s):
    m = SYM_RE.match(s.strip())
    if not m: return None
    root, yymmdd, right, strike_str = m.groups()
    return {
        "expiry": datetime.strptime(yymmdd, "%y%m%d").date(),
        "right": right,
        "strike": int(strike_str) / 1000,
    }


def make_sym(expiry, strike, right):
    yy = expiry.strftime("%y%m%d")
    sk = f"{int(round(strike * 1000)):08d}"
    return f"AVGO  {yy}{right}{sk}"


# ─── Load AVGO spot ─────────────────────────────────────────────────────────
def load_avgo_spot():
    cache = Path(__file__).parent / "avgo_spot_cache.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        return df
    print("Fetching AVGO daily OHLC...")
    df = yf.Ticker("AVGO").history(start="2025-01-01", end="2026-01-15")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


# ─── IC builder + simulator ────────────────────────────────────────────────
def find_strike(strikes_set, target, spacing=2.5):
    """Find strike closest to target, snap to native spacing."""
    snapped = round(target / spacing) * spacing
    if snapped in strikes_set:
        return snapped
    # Walk outward if exact not available
    for delta in [2.5, 5, 7.5, 10, 12.5, 15]:
        for sign in [+1, -1]:
            try_strike = snapped + sign * delta
            if try_strike in strikes_set:
                return try_strike
    return None


def at_or_after_ts(df, target_ts):
    """Get the first row at or after target timestamp."""
    sub = df[df.index >= target_ts]
    if sub.empty: return None
    return sub.iloc[0]


def simulate_ic_day(day_df, trade_date, entry_ts, expiry_date, spot_entry,
                    spot_close, otm_pct, wing_width):
    """Simulate one IC trade for one day.

    Returns dict with results or skip reason.
    """
    # Filter to chosen expiry
    exp_df = day_df[day_df["expiry"] == expiry_date]
    if exp_df.empty:
        return {"skip": "no expiry data"}

    strikes_set = set(exp_df["strike"].unique())

    # Target strikes
    short_put_target = spot_entry * (1 - otm_pct)
    short_call_target = spot_entry * (1 + otm_pct)

    sp_k = find_strike(strikes_set, short_put_target)
    sc_k = find_strike(strikes_set, short_call_target)
    if sp_k is None or sc_k is None:
        return {"skip": "no short strikes"}

    lp_k = find_strike(strikes_set, sp_k - wing_width)
    lc_k = find_strike(strikes_set, sc_k + wing_width)
    if lp_k is None or lc_k is None:
        return {"skip": "no wing strikes"}

    # Get quote streams for all 4 legs
    def get_leg(strike, right):
        return exp_df[(exp_df["strike"] == strike) & (exp_df["right"] == right)]

    sp_q = get_leg(sp_k, "P")
    lp_q = get_leg(lp_k, "P")
    sc_q = get_leg(sc_k, "C")
    lc_q = get_leg(lc_k, "C")
    if any(d.empty for d in [sp_q, lp_q, sc_q, lc_q]):
        return {"skip": "missing leg quotes"}

    # Entry: at entry_ts, get bid/ask for each leg
    e_sp = at_or_after_ts(sp_q, entry_ts)
    e_lp = at_or_after_ts(lp_q, entry_ts)
    e_sc = at_or_after_ts(sc_q, entry_ts)
    e_lc = at_or_after_ts(lc_q, entry_ts)
    if any(x is None for x in [e_sp, e_lp, e_sc, e_lc]):
        return {"skip": "no entry quote at 10:30"}

    # Natural credit: sell shorts at bid, buy wings at ask
    credit = (float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"])
              - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"]))
    if credit < 0.15:
        return {"skip": f"credit too thin ({credit:.2f})"}

    actual_put_width = sp_k - lp_k
    actual_call_width = lc_k - sc_k
    max_width = max(actual_put_width, actual_call_width)
    max_loss_total = (max_width - credit) * 100

    stop_debit = credit * STOP_MULT
    pt_debit = credit * (1 - PT_FRAC)

    # Determine exit deadline
    if expiry_date == trade_date:
        # 0DTE: settle at 4PM ET = 20:00 UTC
        exit_deadline = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC).replace(hour=20, minute=0)
    else:
        # Multi-day: hold position open; need to scan future days too
        # For simplicity in 2025, expand the day_df to include all days up to expiry
        exit_deadline = datetime.combine(expiry_date, datetime.min.time(), tzinfo=UTC).replace(hour=20, minute=0)

    # Build aligned quote frame for SL/PT walk
    # For 0DTE, all legs are in day_df. For multi-day, we'd need to load future parquets.
    # Simpler approach for now: combine the relevant minute bars per leg
    legs = {
        "sp": sp_q[["bid_px_00", "ask_px_00"]].rename(columns={"bid_px_00": "bid_sp", "ask_px_00": "ask_sp"}),
        "lp": lp_q[["bid_px_00", "ask_px_00"]].rename(columns={"bid_px_00": "bid_lp", "ask_px_00": "ask_lp"}),
        "sc": sc_q[["bid_px_00", "ask_px_00"]].rename(columns={"bid_px_00": "bid_sc", "ask_px_00": "ask_sc"}),
        "lc": lc_q[["bid_px_00", "ask_px_00"]].rename(columns={"bid_px_00": "bid_lc", "ask_px_00": "ask_lc"}),
    }
    aligned = legs["sp"].join(legs["lp"], how="inner").join(legs["sc"], how="inner").join(legs["lc"], how="inner")
    aligned = aligned[(aligned.index >= entry_ts) & (aligned.index <= exit_deadline)]

    exit_reason = None; exit_value = None; exit_ts = None
    for ts, row in aligned.iterrows():
        # Close debit: buy back shorts at ask, sell wings at bid
        debit_to_close = (float(row["ask_sp"]) + float(row["ask_sc"])
                          - float(row["bid_lp"]) - float(row["bid_lc"]))
        if debit_to_close >= stop_debit:
            exit_value = debit_to_close
            exit_reason = "stop_loss"
            exit_ts = ts
            break
        if debit_to_close <= pt_debit:
            exit_value = debit_to_close
            exit_reason = "profit_target"
            exit_ts = ts
            break

    if exit_value is None:
        # Settle at expiry against AVGO close
        sp_int = max(0, sp_k - spot_close)
        lp_int = max(0, lp_k - spot_close)
        sc_int = max(0, spot_close - sc_k)
        lc_int = max(0, spot_close - lc_k)
        exit_value = sp_int - lp_int + sc_int - lc_int
        exit_reason = "expiry"
        exit_ts = exit_deadline

    pnl = (credit - exit_value) * 100  # 1 contract
    return {
        "trade_date": trade_date,
        "expiry_date": expiry_date,
        "dte": (expiry_date - trade_date).days,
        "spot_entry": spot_entry,
        "spot_close": spot_close,
        "otm_pct": otm_pct * 100,
        "wing_width": wing_width,
        "sp_k": sp_k, "lp_k": lp_k, "sc_k": sc_k, "lc_k": lc_k,
        "actual_put_w": actual_put_width,
        "actual_call_w": actual_call_width,
        "credit": credit,
        "credit_dollars": credit * 100,
        "max_loss_dollars": max_loss_total,
        "exit_reason": exit_reason,
        "exit_value": exit_value,
        "pnl": pnl,
        "win": pnl > 0,
    }


# ─── Load all data once ────────────────────────────────────────────────────
def load_day(trade_date):
    f = DATA_DIR / f"avgo_cbbo_{trade_date.strftime('%Y%m%d')}.parquet"
    if not f.exists(): return None
    df = pd.read_parquet(f)
    # Parse symbols
    parsed = df["symbol"].apply(parse_sym)
    df["expiry"] = parsed.apply(lambda x: x["expiry"] if x else None)
    df["right"] = parsed.apply(lambda x: x["right"] if x else None)
    df["strike"] = parsed.apply(lambda x: x["strike"] if x else None)
    df = df.dropna(subset=["expiry", "right", "strike"])
    return df


def load_multi_day(start_date, end_date):
    """Load all parquets in date range and concatenate."""
    frames = []
    current = start_date
    while current <= end_date:
        df = load_day(current)
        if df is not None:
            frames.append(df)
        current += timedelta(days=1)
    if not frames: return None
    combined = pd.concat(frames)
    return combined.sort_index()


# ─── Main backtest sweep ────────────────────────────────────────────────────
def main():
    spot_df = load_avgo_spot()
    print(f"AVGO spot data: {len(spot_df)} days")

    # Get all available days from parquet files
    parquet_files = sorted(DATA_DIR.glob("avgo_cbbo_*.parquet"))
    trade_dates = []
    for f in parquet_files:
        ds = f.stem.replace("avgo_cbbo_", "")
        trade_dates.append(date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])))
    print(f"Parquet files: {len(trade_dates)} days  ({trade_dates[0]} → {trade_dates[-1]})")

    # Sweep variants
    OTM_PCTS = [0.01, 0.02, 0.03, 0.05]
    WIDTHS = [5, 10, 15, 25]
    DTE_BUCKETS = [(0, 0, "0DTE"), (1, 3, "1-3DTE"), (4, 7, "4-7DTE"), (8, 14, "8-14DTE")]

    results = []
    t0 = time.time()

    for i, td in enumerate(trade_dates):
        day_df = load_day(td)
        if day_df is None:
            continue

        # AVGO spot at entry (use 10:30 ET = 14:30 UTC if available, else daily open)
        ts_spot = pd.Timestamp(td)
        if ts_spot not in spot_df.index:
            continue
        # Use daily open as proxy for 10:30 entry spot (AVGO chain doesn't have spot directly)
        spot_open = float(spot_df.loc[ts_spot, "Open"])
        spot_close = float(spot_df.loc[ts_spot, "Close"])
        spot_high = float(spot_df.loc[ts_spot, "High"])
        spot_low = float(spot_df.loc[ts_spot, "Low"])
        # Proxy for 10:30 spot: open + 0.3*(close - open) — first hour drift estimate
        spot_entry = spot_open + 0.3 * (spot_close - spot_open)

        entry_ts = datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
            hour=ENTRY_HOUR_ET, minute=ENTRY_MIN_ET).astimezone(UTC)

        # Available expiries on this day
        available_expiries = sorted(day_df["expiry"].unique())

        for otm in OTM_PCTS:
            for w in WIDTHS:
                for dte_lo, dte_hi, dte_label in DTE_BUCKETS:
                    # Find first expiry in this DTE bucket
                    target_exp = None
                    for exp in available_expiries:
                        dte = (exp - td).days
                        if dte_lo <= dte <= dte_hi:
                            target_exp = exp
                            break
                    if target_exp is None:
                        continue
                    # Get expiry close price for settlement
                    exp_ts = pd.Timestamp(target_exp)
                    if exp_ts not in spot_df.index:
                        # Use last known close for now
                        continue
                    exp_close = float(spot_df.loc[exp_ts, "Close"])

                    # For multi-day expiries, we need to load all days from td to target_exp
                    if dte_lo > 0:
                        multi_df = load_multi_day(td, target_exp)
                        sim_df = multi_df if multi_df is not None else day_df
                    else:
                        sim_df = day_df

                    result = simulate_ic_day(sim_df, td, entry_ts, target_exp,
                                              spot_entry, exp_close, otm, w)
                    result["dte_bucket"] = dte_label
                    results.append(result)

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(trade_dates)} days, {len(results)} sims, {elapsed:.0f}s")

    df = pd.DataFrame(results)
    df.to_csv("/tmp/avgo_ic_2025_results.csv", index=False)

    print()
    print("=" * 110)
    print(f"AVGO IC BACKTEST 2025 — TOTAL SIMULATIONS: {len(df)}")
    print(f"  Successful: {df['pnl'].notna().sum()}")
    print(f"  Skipped: {df['skip'].notna().sum() if 'skip' in df.columns else 0}")
    print("=" * 110)

    traded = df[df["pnl"].notna()].copy()

    # === Summary by (OTM%, width, DTE bucket) ===
    print(f"\n{'OTM%':<6}{'Width':<7}{'DTE':<10}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'TotalP&L':<11}{'AvgP&L':<9}{'Best':<9}{'Worst':<10}{'Stops':<7}")
    print("-" * 110)
    summary_rows = []
    for otm in [1.0, 2.0, 3.0, 5.0]:
        for w in [5, 10, 15, 25]:
            for dte_label in ["0DTE", "1-3DTE", "4-7DTE", "8-14DTE"]:
                sub = traded[(traded["otm_pct"] == otm) & (traded["wing_width"] == w) & (traded["dte_bucket"] == dte_label)]
                if len(sub) < 5:
                    continue
                n = len(sub)
                wr = (sub["pnl"] > 0).mean() * 100
                avg_cr = sub["credit_dollars"].mean()
                total = sub["pnl"].sum()
                avg = sub["pnl"].mean()
                best = sub["pnl"].max()
                worst = sub["pnl"].min()
                stops = (sub["exit_reason"] == "stop_loss").sum()
                print(f"{otm:<5.0f}%{w:<7}{dte_label:<10}{n:<5}{wr:<6.0f}%${avg_cr:<7.0f}${total:<+10,.0f}${avg:<+8.0f}${best:<+8.0f}${worst:<+9.0f}{stops:<7}")
                summary_rows.append({
                    "otm_pct": otm, "wing_width": w, "dte": dte_label,
                    "n": n, "win_rate": wr, "avg_credit": avg_cr,
                    "total_pnl": total, "avg_pnl": avg,
                    "best": best, "worst": worst,
                    "stops": stops, "stop_rate": stops/n*100,
                })

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv("/tmp/avgo_ic_2025_summary.csv", index=False)

    print("\n" + "=" * 110)
    print("TOP 10 PROFITABLE VARIANTS")
    print("=" * 110)
    top = summary_df.sort_values("total_pnl", ascending=False).head(10)
    print(top.to_string(index=False))

    print("\n" + "=" * 110)
    print("BOTTOM 5 (worst variants)")
    print("=" * 110)
    bot = summary_df.sort_values("total_pnl").head(5)
    print(bot.to_string(index=False))


if __name__ == "__main__":
    main()
