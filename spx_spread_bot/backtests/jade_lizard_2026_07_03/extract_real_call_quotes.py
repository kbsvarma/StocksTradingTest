"""Extract REAL SPXW call-spread quotes from the local Databento cache.

Two outputs (both keyed to the 453 real BPS trade days):
  entry_calibration.csv — real 09:45 ET call-spread credits for every cached
      call strike pair (widths 5/10/25) on each trade's expiry, next to the
      Black-Scholes estimate used by backtests/jade_lizard_analysis.py.
  eod_decay.csv — for cached full-day call pairs: real credit at 09:45 vs
      real mark at 15:45/EOD → how much of the call credit is actually kept
      when the position is closed same-day (the live bot's T+0 convention).

No network. Reads backtests/ten_year/raw_dbn_cache only.

Crash-resilient: appends rows per trade-day and records progress in
extract_progress.txt; on restart, already-done days are skipped.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BASE = HERE.parent  # backtests/
sys.path.insert(0, str(BASE))
from jade_lizard_analysis import call_spread_credit  # noqa: E402  BSM helper

CACHE = BASE / "ten_year" / "raw_dbn_cache"
ET = ZoneInfo("America/New_York")
WIDTHS = [5.0, 10.0, 25.0]

CAL_CSV = HERE / "entry_calibration.csv"
DEC_CSV = HERE / "eod_decay.csv"
PROGRESS = HERE / "extract_progress.txt"

CAL_COLS = ["date", "expiry", "dte", "spx_entry", "vix", "k_short", "k_long",
            "width", "otm_pct", "real_credit", "bsm_credit"]
DEC_COLS = ["date", "expiry", "dte", "spx_entry", "vix", "k_short", "k_long",
            "width", "otm_pct", "credit_0945", "mark_1545", "mark_last",
            "bsm_credit", "exit_reason", "bps_pnl"]


def occ(sym: str) -> tuple[str, str, float]:
    """-> (yymmdd, right, strike)"""
    return sym[6:12], sym[12], int(sym[13:21]) / 1000.0


def append_rows(path: Path, rows: list[dict], cols: list[str]) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)[cols]
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def main() -> None:
    done: set[str] = set()
    if PROGRESS.exists():
        done = set(PROGRESS.read_text().split())
        print(f"resuming: {len(done)} trade days already done")

    idx = pd.read_parquet(HERE / "cache_index.parquet")
    idx = idx[idx["schema"] == "cbbo-1m"].copy()
    idx["day"] = pd.to_datetime(idx["start_ns"], utc=True).dt.date
    idx["span_h"] = (idx["end_ns"] - idx["start_ns"]) / 3.6e12
    idx["symlist"] = idx["symbols"].map(json.loads)
    idx["calls"] = idx["symlist"].map(lambda L: [s for s in L if s[12] == "C"])

    trades = pd.read_csv(
        BASE / "ten_year" / "trade_results.csv", parse_dates=["Date"]
    )
    trades["day"] = trades["Date"].dt.date
    trades["expiry_yymmdd"] = pd.to_datetime(trades["Expiry"]).dt.strftime("%y%m%d")

    by_day = {d: g for d, g in idx.groupby("day")}
    n_no_entry_file = 0

    for ti, t in enumerate(trades.itertuples()):
        day: date = t.day
        key = day.isoformat()
        if key in done:
            continue
        cal_rows: list[dict] = []
        decay_rows: list[dict] = []
        g = by_day.get(day)
        if g is None:
            n_no_entry_file += 1
            with PROGRESS.open("a") as fh:
                fh.write(key + "\n")
            continue
        spx = float(t._3)  # 'SPX price at entry'
        vix = float(t._4)  # 'VIX at entry'
        exp = t.expiry_yymmdd

        entry_dt = datetime.combine(day, dt_time(9, 45), ET)
        eod_dt = datetime.combine(day, dt_time(15, 45), ET)

        # ── 1. entry snapshots: all small-window files with calls ──────────
        snap_files = g[(g.span_h < 0.1) & (g.calls.map(len) > 0)]
        quotes: dict[str, tuple[float, float]] = {}
        for r in snap_files.itertuples():
            try:
                df = db.DBNStore.from_file(CACHE / r.path).to_df()
            except Exception:
                continue
            if df.empty:
                continue
            if "publisher_id" in df.columns:
                df = df[df["publisher_id"] == 30]
            for sym, sub in df.groupby("symbol"):
                row = sub.iloc[-1]
                b, a = float(row["bid_px_00"]), float(row["ask_px_00"])
                if b > 0 and a > 0:
                    quotes[sym] = (b, a)
        # calls on this trade's expiry
        kmap = {}
        for sym, (b, a) in quotes.items():
            e, right, k = occ(sym)
            if right == "C" and e == exp:
                kmap[k] = (b + a) / 2.0
        for k_short in sorted(kmap):
            otm = k_short / spx - 1.0
            if otm < -0.005 or otm > 0.045:
                continue
            for w in WIDTHS:
                k_long = k_short + w
                if k_long not in kmap:
                    continue
                real = kmap[k_short] - kmap[k_long]
                bsm = call_spread_credit(spx, vix, k_short, k_long)
                cal_rows.append(
                    dict(
                        date=key,
                        expiry=t.Expiry,
                        dte=int(t.DTE),
                        spx_entry=spx,
                        vix=vix,
                        k_short=k_short,
                        k_long=k_long,
                        width=w,
                        otm_pct=round(otm, 5),
                        real_credit=round(real, 3),
                        bsm_credit=round(bsm, 3),
                    )
                )

        # ── 2. full-day call pairs → same-day decay ────────────────────────
        fd = g[(g.span_h > 1) & (g.calls.map(len) == 2)]
        seen_pairs = set()
        for r in fd.itertuples():
            c1, c2 = sorted(r.calls)
            e1, _, k1 = occ(c1)
            e2, _, k2 = occ(c2)
            if e1 != exp or e2 != exp or (c1, c2) in seen_pairs or k2 <= k1:
                continue
            seen_pairs.add((c1, c2))
            try:
                df = db.DBNStore.from_file(CACHE / r.path).to_df()
            except Exception:
                continue
            if df.empty:
                continue
            if "publisher_id" in df.columns:
                df = df[df["publisher_id"] == 30]
            df = df[df["symbol"].isin([c1, c2])]
            if df.empty:
                continue
            ts = pd.to_datetime(df.index, utc=True).tz_convert(ET)
            df = df.assign(ts_et=ts)
            wide = (
                df.groupby(["ts_et", "symbol"])[["bid_px_00", "ask_px_00"]]
                .last()
                .unstack("symbol")
                .sort_index()
                .ffill()
            )

            def spread_mid(row) -> float | None:
                try:
                    sb = row[("bid_px_00", c1)]
                    sa = row[("ask_px_00", c1)]
                    lb = row[("bid_px_00", c2)]
                    la = row[("ask_px_00", c2)]
                except KeyError:
                    return None
                if pd.isna(sb) or pd.isna(sa) or sb <= 0 or sa <= 0:
                    return None
                lb = 0.0 if pd.isna(lb) else lb
                la = 0.0 if pd.isna(la) else la
                return ((sb - la) + (sa - lb)) / 2.0

            early = wide[wide.index <= entry_dt + timedelta(minutes=3)]
            late = wide[wide.index <= eod_dt]
            if early.empty or late.empty:
                continue
            credit0 = spread_mid(early.iloc[-1])
            mark_eod = spread_mid(late.iloc[-1])
            mark_last = spread_mid(wide.iloc[-1])
            if credit0 is None:
                continue
            decay_rows.append(
                dict(
                    date=key,
                    expiry=t.Expiry,
                    dte=int(t.DTE),
                    spx_entry=spx,
                    vix=vix,
                    k_short=k1,
                    k_long=k2,
                    width=k2 - k1,
                    otm_pct=round(k1 / spx - 1.0, 5),
                    credit_0945=round(credit0, 3),
                    mark_1545=None if mark_eod is None else round(mark_eod, 3),
                    mark_last=None if mark_last is None else round(mark_last, 3),
                    bsm_credit=round(call_spread_credit(spx, vix, k1, k2), 3),
                    exit_reason=t._14,  # 'Exit reason'
                    bps_pnl=float(t._12),  # 'Total PnL'
                )
            )

        append_rows(CAL_CSV, cal_rows, CAL_COLS)
        append_rows(DEC_CSV, decay_rows, DEC_COLS)
        with PROGRESS.open("a") as fh:
            fh.write(key + "\n")
        if ti % 50 == 0:
            print(f"{ti}/{len(trades)} {key}", flush=True)

    print(f"trades={len(trades)} no_entry_file={n_no_entry_file}")
    for p, name in [(CAL_CSV, "entry_calibration"), (DEC_CSV, "eod_decay")]:
        if p.exists():
            df = pd.read_csv(p)
            print(f"{name}: rows={len(df)} days={df['date'].nunique()}")


if __name__ == "__main__":
    main()
