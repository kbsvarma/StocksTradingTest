"""NDXP 0DTE BPS adversarial re-verification — corrected methodology.

Re-runs the strategy from ndxp_0dte_strict_results with the look-ahead
filters removed and executable pricing, per audit_notes.md:

  - Entry 10:30 ET (same as live SPXW bot), quotes from local Databento
    CBBO-1m caches (consolidated, publisher 30). NO network calls.
  - Strike selection per the ORIGINAL study's config: short strike =
    nearest listed strike to NDX_open * (1 - 1.0%), long = short - 100.
    (Original used the daily open as the OTM reference; open is known
    by 10:30 so this is legitimate.)
  - Credit = short_mid - long_mid at 10:30 (mid variant) and
    short_bid - long_ask (executable variant).
  - SL at 2.0x credit, mark-to-market on 1-min spread mid; SL exit
    costed at mid (mid variant) and short_ask - long_bid (exec variant).
  - No SL hit -> PM settlement: intrinsic at official NDX close
    (NDXP weeklies are PM-settled).
  - Slippage sensitivity: 0.10 / 0.25 / 0.50 pts per spread per side
    (entry always; exit side only when the position is traded out).
  - Filters recorded as COLUMNS (not silent skips) so variants can be
    aggregated later: dir_up_1030 (put-call parity spot at 10:30 > open,
    the legit analogue of the bot's direction filter), vix_prev_close.
  - The original's look-ahead filters (same-day close vs open, same-day
    VIX close) are also recorded as columns purely to DEMONSTRATE the bug,
    never to gate entry.

CRASH-RESILIENT: appends one row per day to trades_corrected.csv (flush
per row); on restart, days already present are skipped. Summary files are
written via tmp + atomic os.replace. Never deletes existing outputs.

Run: /opt/anaconda3/bin/python3 reverify.py [start_year] [end_year]
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BT = HERE.parent
DATA = BT / "narrow_width_databento_2026_05_21"
PUTS = DATA / "ndxp_p_cbbo_1m"
CALLS = DATA / "ndxp_c_cbbo_1m"
SPOT_FP = DATA / "spot_cache_NDX.parquet"
VIX_FP = BT / "edge_hunt_cache" / "vix_panel.parquet"

TRADES_FP = HERE / "trades_corrected.csv"
SKIPS_FP = HERE / "skips_corrected.csv"
LOG_FP = HERE / "reverify.log"

OTM_PCT = 0.010          # per original study (measured 0.999% mean from its trades.csv)
WIDTH = 100              # per original study
SL_MULT = 2.0
MULT = 100.0             # $ per NDX point
ENTRY = ("10:28", "10:32")  # accept nearest bar in this window, prefer 10:30

_RX = re.compile(r"P(\d{8})$")

TRADE_COLS = [
    "date", "ndx_open", "ndx_close", "vix_prev_close",
    "parity_1030", "dir_up_1030", "lookahead_close_up", "lookahead_vix_ok",
    "short_strike", "long_strike", "entry_bar",
    "cred_mid", "cred_exec", "sl_fired", "sl_bar",
    "exit_mid", "exit_exec", "settle_intrinsic",
    "pnl_mid", "pnl_exec", "pnl_slip10", "pnl_slip25", "pnl_slip50",
]
SKIP_COLS = ["date", "reason"]


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_FP, "a") as fh:
        fh.write(line + "\n")


def append_row(fp: Path, cols: list[str], row: dict) -> None:
    new = not fp.exists()
    with open(fp, "a") as fh:
        if new:
            fh.write(",".join(cols) + "\n")
        fh.write(",".join(str(row.get(c, "")) for c in cols) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def done_dates() -> set[str]:
    got = set()
    for fp in (TRADES_FP, SKIPS_FP):
        if fp.exists():
            try:
                got |= set(pd.read_csv(fp, usecols=["date"])["date"].astype(str))
            except Exception:
                pass
    return got


def load_quotes(fp: Path) -> pd.DataFrame:
    """Per-(minute ET, strike) last bid/ask. Basic sanity only: bid<=ask, ask>0."""
    df = pd.read_parquet(fp)
    if df.empty:
        return pd.DataFrame()
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["ask_px_00"] > 0) & (df["bid_px_00"] >= 0)
            & (df["bid_px_00"] <= df["ask_px_00"])]
    if df.empty:
        return pd.DataFrame()
    strikes = df["symbol"].str.slice(13, 21).astype(int) / 1000.0
    out = pd.DataFrame({
        "ts": df.index.tz_convert("America/New_York").floor("min"),
        "strike": strikes.values,
        "bid": df["bid_px_00"].values,
        "ask": df["ask_px_00"].values,
    })
    return out.groupby(["ts", "strike"]).last()


def entry_snapshot(q: pd.DataFrame, day: pd.Timestamp):
    """Quotes at the bar nearest 10:30 within ENTRY window. -> (bar_ts, frame)"""
    tz = "America/New_York"
    lo = pd.Timestamp(f"{day.date()} {ENTRY[0]}", tz=tz)
    hi = pd.Timestamp(f"{day.date()} {ENTRY[1]}", tz=tz)
    target = pd.Timestamp(f"{day.date()} 10:30", tz=tz)
    ts_all = q.index.get_level_values("ts").unique()
    win = ts_all[(ts_all >= lo) & (ts_all <= hi)]
    if len(win) == 0:
        return None, None
    bar = min(win, key=lambda t: abs(t - target))
    return bar, q.xs(bar, level="ts")


def parity_spot(cq: pd.DataFrame, pq: pd.DataFrame, bar: pd.Timestamp):
    try:
        c = cq.xs(bar, level="ts")
        p = pq.xs(bar, level="ts")
    except KeyError:
        return None
    m = pd.DataFrame({
        "c": (c["bid"] + c["ask"]) / 2,
        "p": (p["bid"] + p["ask"]) / 2,
    }).dropna()
    if m.empty:
        return None
    m["d"] = (m["c"] - m["p"]).abs()
    best = m.sort_values("d").iloc[0]
    return float(best.name) + float(best["c"]) - float(best["p"])


def run(y0: int, y1: int) -> None:
    spot = pd.read_parquet(SPOT_FP, engine="fastparquet")
    spot.index = pd.to_datetime(spot.index)
    vix = pd.read_parquet(VIX_FP)
    vix.index = pd.to_datetime(vix.index)
    seen = done_dates()
    files = sorted(PUTS.glob("ndxp_p_cbbo_*.parquet"))
    files = [f for f in files if y0 <= int(f.stem[-8:-4]) <= y1]
    log(f"START window={y0}-{y1} files={len(files)} already_done={len(seen)}")

    for i, fp in enumerate(files):
        ds = fp.stem[-8:]
        date = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
        if date in seen:
            continue
        day = pd.Timestamp(date)

        def skip(reason):
            append_row(SKIPS_FP, SKIP_COLS, {"date": date, "reason": reason})

        if day not in spot.index:
            skip("no_spot"); continue
        ndx_open = float(spot.loc[day, "Open"])
        ndx_close = float(spot.loc[day, "Close"])
        prior = vix.loc[:day]
        vprev = float(prior.iloc[-2]["vix_close"]) if (len(prior) >= 2 and prior.index[-1] == day) \
            else (float(prior.iloc[-1]["vix_close"]) if len(prior) else np.nan)
        vix_open_td = float(prior.iloc[-1]["vix_open"]) if (len(prior) and prior.index[-1] == day) else np.nan
        vix_close_td = float(prior.iloc[-1]["vix_close"]) if (len(prior) and prior.index[-1] == day) else np.nan

        pq = load_quotes(fp)
        if pq.empty:
            skip("no_put_quotes"); continue
        bar, snap = entry_snapshot(pq, day)
        if bar is None:
            skip("no_1030_bar"); continue

        cfp = CALLS / fp.name.replace("_p_", "_c_")
        par = None
        if cfp.exists():
            cq = load_quotes(cfp)
            if not cq.empty:
                par = parity_spot(cq, pq, bar)

        # strike selection per original config: 1% OTM from the OPEN, 100 wide
        strikes = sorted(snap.index.tolist())
        target = ndx_open * (1 - OTM_PCT)
        cand = [k for k in strikes if (k - WIDTH) in strikes]
        if not cand:
            skip("no_100pt_pair"); continue
        sk = min(cand, key=lambda k: abs(k - target))
        if abs(sk - target) > 60:  # nearest listed pair is nowhere near 1% OTM
            skip("no_strike_near_1pct"); continue
        lk = sk - WIDTH
        s = snap.loc[sk]; l = snap.loc[lk]
        cred_mid = float((s["bid"] + s["ask"]) / 2 - (l["bid"] + l["ask"]) / 2)
        cred_exec = float(s["bid"] - l["ask"])
        if cred_mid <= 0.05:
            skip("no_credit"); continue

        # SL walk on 1-min spread mid after entry
        try:
            ss = pq.xs(sk, level="strike")
            ll = pq.xs(lk, level="strike")
        except KeyError:
            skip("no_series"); continue
        m = ss.join(ll, lsuffix="_s", rsuffix="_l", how="inner")
        m = m[m.index > bar]
        m["mid"] = (m["bid_s"] + m["ask_s"]) / 2 - (m["bid_l"] + m["ask_l"]) / 2
        stop = SL_MULT * cred_mid
        sl_fired, sl_bar, exit_mid, exit_exec = False, "", np.nan, np.nan
        hit = m[m["mid"] >= stop]
        if len(hit):
            r = hit.iloc[0]
            sl_fired = True
            sl_bar = str(hit.index[0])
            exit_mid = float(r["mid"])
            exit_exec = float(r["ask_s"] - r["bid_l"])

        settle = max(0.0, sk - ndx_close) - max(0.0, lk - ndx_close)
        if sl_fired:
            pnl_mid = (cred_mid - exit_mid) * MULT
            pnl_exec = (cred_exec - exit_exec) * MULT
            rt_sides = 2
        else:
            pnl_mid = (cred_mid - settle) * MULT
            pnl_exec = (cred_exec - settle) * MULT
            rt_sides = 1

        row = {
            "date": date, "ndx_open": round(ndx_open, 2), "ndx_close": round(ndx_close, 2),
            "vix_prev_close": round(vprev, 2) if vprev == vprev else "",
            "parity_1030": round(par, 1) if par else "",
            "dir_up_1030": (par > ndx_open) if par else "",
            "lookahead_close_up": ndx_close > ndx_open,
            "lookahead_vix_ok": (12 <= vix_close_td <= 25) if vix_close_td == vix_close_td else "",
            "short_strike": int(sk), "long_strike": int(lk), "entry_bar": str(bar),
            "cred_mid": round(cred_mid, 2), "cred_exec": round(cred_exec, 2),
            "sl_fired": sl_fired, "sl_bar": sl_bar,
            "exit_mid": round(exit_mid, 2) if exit_mid == exit_mid else "",
            "exit_exec": round(exit_exec, 2) if exit_exec == exit_exec else "",
            "settle_intrinsic": round(settle, 2),
            "pnl_mid": round(pnl_mid, 0), "pnl_exec": round(pnl_exec, 0),
            "pnl_slip10": round(pnl_mid - 0.10 * rt_sides * MULT, 0),
            "pnl_slip25": round(pnl_mid - 0.25 * rt_sides * MULT, 0),
            "pnl_slip50": round(pnl_mid - 0.50 * rt_sides * MULT, 0),
        }
        append_row(TRADES_FP, TRADE_COLS, row)
        if (i + 1) % 50 == 0:
            log(f"  {i+1}/{len(files)} processed (last {date})")

    log("scan complete; writing summary")
    write_summary()


def write_summary() -> None:
    t = pd.read_csv(TRADES_FP)
    t["dir_up_1030"] = t["dir_up_1030"].astype(str) == "True"
    t["has_parity"] = t["parity_1030"].notna() & (t["parity_1030"].astype(str) != "")
    variants = {
        "A_all_days": t,
        "B_dir_filter_1030": t[t["has_parity"] & t["dir_up_1030"]],
        "C_dir_plus_vix_prev_lt25": t[t["has_parity"] & t["dir_up_1030"] & (t["vix_prev_close"] < 25)],
        "X_LOOKAHEAD_repro_close_up": t[t["lookahead_close_up"]],
    }
    rows = []
    for name, v in variants.items():
        if not len(v):
            continue
        for pcol in ["pnl_mid", "pnl_exec", "pnl_slip10", "pnl_slip25", "pnl_slip50"]:
            p = v[pcol]
            rows.append({
                "variant": name, "pnl_basis": pcol, "trades": len(v),
                "win_rate_pct": round((p > 0).mean() * 100, 1),
                "sl_rate_pct": round(v["sl_fired"].mean() * 100, 1),
                "avg_credit_usd": round(v["cred_mid"].mean() * 100, 0),
                "net_pnl": round(p.sum(), 0),
                "pnl_per_trade": round(p.mean(), 0),
                "worst_trade": round(p.min(), 0),
                "best_trade": round(p.max(), 0),
            })
    out = pd.DataFrame(rows)
    tmp = HERE / "summary_corrected.csv.tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, HERE / "summary_corrected.csv")

    t["year"] = t["date"].str.slice(0, 4)
    yr = t.groupby("year").agg(trades=("pnl_mid", "size"),
                               win_rate_pct=("pnl_mid", lambda p: round((p > 0).mean() * 100, 1)),
                               net_pnl_mid=("pnl_mid", "sum"),
                               net_pnl_slip25=("pnl_slip25", "sum")).reset_index()
    tmp2 = HERE / "yearly_corrected.csv.tmp"
    yr.to_csv(tmp2, index=False)
    os.replace(tmp2, HERE / "yearly_corrected.csv")
    log(f"summary written: {len(t)} trade rows")


if __name__ == "__main__":
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    run(y0, y1)
