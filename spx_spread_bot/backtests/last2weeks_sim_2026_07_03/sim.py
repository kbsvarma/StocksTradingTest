"""Simulate the live SPX 0DTE BPS bot over 2026-06-22..2026-07-03.

Applies the EXACT gate order from webull_bot/main.py:
  run_day (weekdays) -> entry_window(10:30) -> VIX gate -> direction filter -> find_best_spread(min_credit)

Then intraday 2x-credit mark stop, else PM settlement. 1 contract. Slippage sensitivity on stop exit.

Incremental per_day.csv; skip-completed on restart; atomic report is written by build_report separately.
"""
import csv
from datetime import datetime, time as dtime
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
OPT_DIR = HERE / "spxw_p_cbbo_1m"
PER_DAY = HERE / "per_day.csv"

# ── config (mirrors webull_bot/config.yaml) ──
OTM_PCT = 0.010
WIDTH = 50
MIN_CREDIT = 1.20
VIX_MIN, VIX_MAX = 12.0, 25.0
VIX_MAX_RISE = 3.0
STOP_MULT = 2.0
SLIPS = [0.05, 0.10, 0.15]

DAYS = ["20260622","20260623","20260624","20260625","20260626",
        "20260629","20260630","20260701","20260702","20260703"]

FIELDS = ["date","weekday","traded","skip_reason","short","long","credit",
          "spx_open","spx_1030","spx_close","vix_open","vix_1030",
          "exit_reason","stop_minute","stop_mark","pnl_pts_slip05","pnl_usd_slip05",
          "pnl_pts_slip10","pnl_usd_slip10","pnl_pts_slip15","pnl_usd_slip15","note"]


def load_spot():
    spx = pd.read_parquet(HERE / "spx_1m.parquet")   # tz America/New_York
    vix = pd.read_parquet(HERE / "vix_1m.parquet")
    return spx, vix


def daybar_close(name, day):
    # daily fallback for close if 1m missing
    df = pd.read_parquet(HERE / f"{name}_daily.parquet") if (HERE / f"{name}_daily.parquet").exists() else None
    return None


def val_at(df, day, hhmm):
    """Value (Close of the minute) at ET hh:mm for given date; None if absent."""
    dd = df[df.index.date == day]
    if len(dd) == 0:
        return None
    h, m = hhmm
    target = dd[(dd.index.hour == h) & (dd.index.minute == m)]
    if len(target):
        return float(target["Close"].iloc[0])
    return None


def session_open(df, day):
    dd = df[df.index.date == day].between_time("09:30", "16:00")
    if len(dd) == 0:
        return None
    return float(dd["Open"].iloc[0])


def session_close(df, day):
    dd = df[df.index.date == day].between_time("09:30", "16:00")
    if len(dd) == 0:
        return None
    return float(dd["Close"].iloc[-1])


def strike_from_symbol(sym):
    # 'SPXW  260622P07260000' -> 7260.0
    return int(sym.split("P")[-1]) / 1000.0


def build_leg_frames(opt_df):
    """Return dict[strike] -> DataFrame(minute_ET -> bid,ask) using ts_recv."""
    opt_df = opt_df.copy()
    opt_df["strike"] = opt_df["symbol"].map(strike_from_symbol)
    # index is ts_recv UTC -> convert to ET
    idx_et = opt_df.index.tz_convert("America/New_York")
    opt_df = opt_df.assign(et=idx_et)
    legs = {}
    for k, g in opt_df.groupby("strike"):
        gg = g[["et", "bid_px_00", "ask_px_00"]].copy()
        gg = gg.set_index("et").sort_index()
        legs[k] = gg
    return legs


def mid_at(legs, strike, et_ts):
    g = legs.get(strike)
    if g is None:
        return None
    # last quote at or before et_ts (forward fill)
    sub = g[g.index <= et_ts]
    if len(sub) == 0:
        return None
    bid = sub["bid_px_00"].iloc[-1]
    ask = sub["ask_px_00"].iloc[-1]
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0:
        # try last row with a valid two-sided quote
        valid = sub[(sub["bid_px_00"] > 0) & (sub["ask_px_00"].notna()) & (sub["ask_px_00"] > 0)]
        if len(valid) == 0:
            return None
        bid = valid["bid_px_00"].iloc[-1]; ask = valid["ask_px_00"].iloc[-1]
    return (float(bid) + float(ask)) / 2.0


def spread_mid(legs, short_k, long_k, et_ts):
    sm = mid_at(legs, short_k, et_ts)
    lm = mid_at(legs, long_k, et_ts)
    if sm is None or lm is None:
        return None
    return round(sm - lm, 2)


def find_best_spread(legs, spx_1030, day, entry_ts):
    """Mirror market_data.find_best_spread: target short = round(spx*0.99/5)*5,
    scan 5 nearest strikes, long = short-50, keep credit>=min_credit, pick closest to 2.0."""
    target_short = round(spx_1030 * (1 - OTM_PCT) / 5) * 5
    available = sorted(legs.keys())
    candidates = sorted(available, key=lambda s: abs(s - target_short))[:5]
    best = None
    best_diff = float("inf")
    for short_k in candidates:
        ideal_long = short_k - WIDTH
        below = [s for s in available if s <= ideal_long]
        if not below:
            continue
        long_k = max(below)
        if short_k - long_k < WIDTH * 0.8:
            continue
        credit = spread_mid(legs, short_k, long_k, entry_ts)
        if credit is None or credit < MIN_CREDIT:
            continue
        diff = abs(credit - 2.0)
        if diff < best_diff:
            best_diff = diff
            best = (short_k, long_k, credit)
    return best


def simulate_day(ds, spx_df, vix_df):
    day = datetime.strptime(ds, "%Y%m%d").date()
    wd = day.strftime("%a")
    row = {f: "" for f in FIELDS}
    row["date"] = str(day); row["weekday"] = wd; row["traded"] = "NO"

    # Reference values
    spx_open = session_open(spx_df, day)
    spx_1030 = val_at(spx_df, day, (10, 30))
    spx_close = session_close(spx_df, day)
    vix_open = session_open(vix_df, day)
    vix_1030 = val_at(vix_df, day, (10, 30))
    row["spx_open"] = round(spx_open,2) if spx_open else ""
    row["spx_1030"] = round(spx_1030,2) if spx_1030 else ""
    row["spx_close"] = round(spx_close,2) if spx_close else ""
    row["vix_open"] = round(vix_open,2) if vix_open else ""
    row["vix_1030"] = round(vix_1030,2) if vix_1030 else ""

    opt_path = OPT_DIR / f"spxw_p_cbbo_{ds}.parquet"

    # 07-03 special: no option data available yet -> INCOMPLETE
    if not opt_path.exists():
        # still evaluate the VIX gate + direction filter from what we have
        gate_notes = []
        if vix_1030 is not None:
            if vix_1030 < VIX_MIN or vix_1030 > VIX_MAX:
                gate_notes.append(f"VIX {vix_1030:.2f} outside band")
            elif vix_open and (vix_1030 - vix_open) > VIX_MAX_RISE:
                gate_notes.append(f"VIX rose {vix_1030-vix_open:.2f}")
        row["exit_reason"] = "INCOMPLETE"
        row["skip_reason"] = "no option data (0DTE not settled / OPRA not published)"
        row["note"] = "07-03 half-day, sim time ~15:37 ET; gates only. " + ("; ".join(gate_notes) if gate_notes else "VIX gate would pass")
        return row

    opt_df = pd.read_parquet(opt_path)
    legs = build_leg_frames(opt_df)

    # entry timestamp = 10:30 ET
    tz = spx_df.index.tz
    entry_ts = pd.Timestamp(datetime.combine(day, dtime(10, 30)), tz="America/New_York")

    # ── GATE 4: VIX ──
    if vix_1030 is None:
        row["skip_reason"] = "no VIX@10:30"; row["exit_reason"] = "SKIP"; return row
    if vix_1030 < VIX_MIN or vix_1030 > VIX_MAX:
        row["skip_reason"] = f"VIX {vix_1030:.2f} outside [{VIX_MIN},{VIX_MAX}]"; row["exit_reason"] = "SKIP"; return row
    if vix_open and (vix_1030 - vix_open) > VIX_MAX_RISE:
        row["skip_reason"] = f"VIX rose {vix_1030-vix_open:.2f}pts from open"; row["exit_reason"] = "SKIP"; return row

    # ── GATE 5: direction filter (SPX@10:30 >= session open) ──
    if spx_1030 is None or spx_open is None:
        row["skip_reason"] = "no SPX ref"; row["exit_reason"] = "SKIP"; return row
    if spx_1030 < spx_open:
        row["skip_reason"] = f"direction: SPX {spx_1030:.2f} < open {spx_open:.2f}"; row["exit_reason"] = "SKIP"; return row

    # ── GATE 6: find_best_spread / min_credit ──
    best = find_best_spread(legs, spx_1030, day, entry_ts)
    if best is None:
        row["skip_reason"] = f"no spread >= {MIN_CREDIT} credit near 1% OTM"; row["exit_reason"] = "SKIP"; return row
    short_k, long_k, credit = best
    row["traded"] = "YES"; row["short"] = short_k; row["long"] = long_k; row["credit"] = round(credit, 2)

    stop_price = round(credit * STOP_MULT, 2)

    # ── intraday: mark per minute 10:30 -> 16:00 (or last available) ──
    minute_index = pd.date_range(start=entry_ts, end=pd.Timestamp(datetime.combine(day, dtime(16, 0)), tz="America/New_York"), freq="1min")
    stop_hit = None
    for ts in minute_index:
        m = spread_mid(legs, short_k, long_k, ts)
        if m is None:
            continue
        if 0 <= m <= WIDTH + 0.01 and m >= stop_price:
            stop_hit = (ts, m)
            break

    if stop_hit is not None:
        ts, mark = stop_hit
        row["exit_reason"] = "STOP"
        row["stop_minute"] = ts.strftime("%H:%M")
        row["stop_mark"] = round(mark, 2)
        for slip, tag in zip(SLIPS, ["05","10","15"]):
            pnl_pts = credit - (mark + slip)
            row[f"pnl_pts_slip{tag}"] = round(pnl_pts, 2)
            row[f"pnl_usd_slip{tag}"] = round(pnl_pts * 100, 2)
    else:
        # PM settlement
        if spx_close is None:
            row["exit_reason"] = "INCOMPLETE"; row["note"] = "no SPX close"; return row
        settle = max(0.0, short_k - spx_close) - max(0.0, long_k - spx_close)
        pnl_pts = credit - settle
        row["exit_reason"] = "EXPIRED"
        row["stop_mark"] = round(settle, 2)
        for tag in ["05","10","15"]:
            row[f"pnl_pts_slip{tag}"] = round(pnl_pts, 2)
            row[f"pnl_usd_slip{tag}"] = round(pnl_pts * 100, 2)
        row["note"] = f"settle_val={settle:.2f} (SPX_close {spx_close:.2f})"
    return row


def load_done():
    done = {}
    if PER_DAY.exists():
        with open(PER_DAY) as f:
            for r in csv.DictReader(f):
                done[r["date"]] = r
    return done


def main():
    spx_df, vix_df = load_spot()
    done = load_done()
    rows = []
    for ds in DAYS:
        day = str(datetime.strptime(ds, "%Y%m%d").date())
        if day in done:
            rows.append(done[day]); print(f"  {ds}: already done, skip"); continue
        r = simulate_day(ds, spx_df, vix_df)
        rows.append(r)
        # incremental write after each day
        with open(PER_DAY, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"  {ds}: {r['traded']} {r['exit_reason']} {r.get('skip_reason','')} "
              f"credit={r['credit']} pnl05={r.get('pnl_usd_slip05','')}")
    print(f"[done] wrote {PER_DAY}")


if __name__ == "__main__":
    main()
