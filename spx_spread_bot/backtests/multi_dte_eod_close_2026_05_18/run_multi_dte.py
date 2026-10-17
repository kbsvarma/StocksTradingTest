"""
Multi-DTE EOD-close backtest for SPX bull put spread strategy.

Variants: 0/1/2/3/7 DTE, each with and without 50% profit target.
ALL variants OPEN at 10:30 ET and CLOSE at 15:45 ET (EOD) — no overnight holds.

DATA HONESTY:
  We only have OHLCV-1d (daily) option data. So 10:30 entry / 15:45 exit
  marks must be APPROXIMATED:
    - Entry mark @ 10:30 ET   ≈  option day `open` (= 9:30 ET print).
    - Exit  mark @ 15:45 ET   ≈  option day `close` (= 16:00 ET print).
  Intraday SL/PT triggers are detected using the option's day high/low:
    - max spread mark intraday  ≈  short_high - long_low   (worst case for short)
    - min spread mark intraday  ≈  short_low  - long_high  (best  case for short)
  These bracket the true intraday path. If the upper bracket >= SL_level we count
  an SL; if the lower bracket <= PT_level we count a PT. Both can happen the same
  day — when ambiguous we apply the worse-case rule (SL fires first) because
  longer-dated spreads tend to move up before they decay.

  Slippage model on close: $0.05 per spread close cost (≈ realistic XSP / 1-lot
  SPXW spread). 0DTE no-PT "expire" leg has no closing cost.

vG-style params:
  OTM_PCT     = 0.0075  (0.75% OTM)
  WIDTH       = 50pt
  MIN_CREDIT  = 1.20  ($)
  STOP_MULT   = 2.0x premium
  TARGET_PCT  = 0.50  (close when mark <= entry * 0.5)
  VIX gate    = 12..25
  Entry time  = 10:30 ET   (proxied by option `open`)
  Exit  time  = 15:45 ET   (proxied by option `close`)
  Direction   = require spx_close >= spx_open  (we don't have 10:30 SPX mark;
                using full-day green-bar proxy biases toward stronger days, but
                noted)
"""
from __future__ import annotations

import csv
import json
import re
import warnings
from dataclasses import dataclass, asdict
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional

import databento as db
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent.parent.parent / "data" / "databento"
OUT_DIR = Path(__file__).resolve().parent
SPXW_DIRS = [
    BASE / "spxw_opt_2014_2015",
    BASE / "spxw_opt_2016_2018",
    BASE / "spxw_opt_2019",
    BASE / "spxw_opt_2020_2022",
    BASE / "spxw_opt",
    BASE / "spxw_opt_2025_2026",
]
SPX_CACHE = Path("/tmp/spx_daily_2014_2026.parquet")
RAW_CACHE = Path("/tmp/spxw_raw_multidte_8day.pkl")

OTM_PCT    = 0.0075
WIDTH      = 50
MIN_CREDIT = 1.20
STOP_MULT  = 2.0
TARGET_PCT = 0.50
VIX_MIN    = 12.0
VIX_MAX    = 25.0
SLIPPAGE_CLOSE = 0.05   # $ per spread per close (round-turn already in entry/exit gap)

OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)
MAX_DTE_TO_LOAD = 10

DTE_VARIANTS = [0, 1, 2, 3, 7]


def next_bday(d: date, n: int) -> date:
    res, added = d, 0
    while added < n:
        res += timedelta(days=1)
        if res.weekday() < 5:
            added += 1
    return res


def load_spx_daily() -> pd.DataFrame:
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
        return df
    import yfinance as yf
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-20",
                      auto_adjust=True, progress=False)
    vix = yf.download("^VIX", start="2014-01-01", end="2026-05-20",
                     auto_adjust=True, progress=False)
    for df_ in (spx, vix):
        if isinstance(df_.columns, pd.MultiIndex):
            df_.columns = [c[0] for c in df_.columns]
    df = spx[["Open", "High", "Low", "Close"]].copy()
    df.columns = ["spx_open", "spx_high", "spx_low", "spx_close"]
    df["vix_close"] = vix["Close"]
    df.index = pd.to_datetime(df.index).normalize()
    df.dropna(subset=["spx_open"], inplace=True)
    df.to_parquet(SPX_CACHE)
    return df


def parse_symbol(sym: str):
    m = OCC_RE.match(sym.strip())
    if not m:
        return None
    try:
        exp = datetime.strptime(m.group("yymmdd"), "%y%m%d").date()
        strike = int(m.group("strike")) / 1000.0
        return m.group("root"), exp, m.group("right"), strike
    except Exception:
        return None


def load_all_spxw() -> dict[date, dict[date, pd.DataFrame]]:
    """raw[file_date][expiry_date] = DataFrame(strike, open, high, low, close) for puts."""
    if RAW_CACHE.exists():
        import pickle
        with RAW_CACHE.open("rb") as f:
            raw = pickle.load(f)
        print(f"raw cache: {len(raw)} trading days")
        return raw

    print("Loading SPXW data (DTE <= %d)..." % MAX_DTE_TO_LOAD)
    raw: dict[date, dict[date, pd.DataFrame]] = {}
    for data_dir in SPXW_DIRS:
        if not data_dir.exists():
            print(f"  missing: {data_dir.name}")
            continue
        files = sorted(data_dir.glob("opra-pillar-*.ohlcv-1d.dbn.zst"))
        print(f"  {data_dir.name:30}  {len(files):4} files")
        for f in files:
            try:
                day_str = f.stem.split(".")[0].split("-")[2]
                file_date = datetime.strptime(day_str, "%Y%m%d").date()
            except Exception:
                continue
            if file_date in raw:
                continue
            try:
                store = db.DBNStore.from_file(str(f))
                df_raw = store.to_df()
            except Exception:
                continue
            by_expiry: dict[date, list[dict]] = {}
            for sym, grp in df_raw.groupby("symbol"):
                parsed = parse_symbol(str(sym).strip())
                if not parsed:
                    continue
                root, exp, right, strike = parsed
                if root not in ("SPXW", "SPX"):
                    continue
                if right != "P":
                    continue
                dte = (exp - file_date).days
                if dte < 0 or dte > MAX_DTE_TO_LOAD:
                    continue
                r = grp.iloc[0]
                by_expiry.setdefault(exp, []).append({
                    "strike": strike,
                    "open":   float(r["open"]),
                    "high":   float(r["high"]),
                    "low":    float(r["low"]),
                    "close":  float(r["close"]),
                })
            if by_expiry:
                raw[file_date] = {exp: pd.DataFrame(rows) for exp, rows in by_expiry.items()}

    print(f"  loaded {len(raw)} trading days")
    import pickle
    with RAW_CACHE.open("wb") as f:
        pickle.dump(raw, f)
    return raw


def build_spread(opts: pd.DataFrame, spx_open: float) -> Optional[dict]:
    if opts is None or opts.empty:
        return None
    target_short = spx_open * (1.0 - OTM_PCT)
    o = opts.copy()
    o["dist"] = (o["strike"] - target_short).abs()
    short_row = o.loc[o["dist"].idxmin()]
    short_strike = float(short_row["strike"])
    long_strike  = short_strike - WIDTH
    exact = o[o["strike"] == long_strike]
    if not exact.empty:
        long_row = exact.iloc[0]
    else:
        below = o[o["strike"] < short_strike]
        if below.empty:
            return None
        long_row = below.loc[(below["strike"] - long_strike).abs().idxmin()]
        long_strike = float(long_row["strike"])
    credit = float(short_row["open"]) - float(long_row["open"])
    if credit < MIN_CREDIT or credit <= 0:
        return None
    return dict(
        short_strike=short_strike,
        long_strike=long_strike,
        credit=credit,
        short_open=float(short_row["open"]),
        long_open=float(long_row["open"]),
        short_high=float(short_row["high"]),
        short_low=float(short_row["low"]),
        short_close=float(short_row["close"]),
        long_high=float(long_row["high"]),
        long_low=float(long_row["low"]),
        long_close=float(long_row["close"]),
    )


@dataclass
class Trade:
    entry_date:   str
    dte:          int
    use_pt:       bool
    short_strike: float
    long_strike:  float
    credit:       float        # dollars (×100)
    exit_mark:    float        # dollars (×100)
    pnl:          float
    win:          bool
    exit_reason:  str
    vix:          float
    spx_open:     float
    spx_close:    float
    capture_pct:  float


def simulate(raw: dict, spx: pd.DataFrame, dte: int, use_pt: bool) -> list[Trade]:
    """Open at 10:30 (proxy=open), close at 15:45 (proxy=close), test SL/PT via intraday bracket."""
    trades: list[Trade] = []
    for day in sorted(raw.keys()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row = spx.loc[ts]
        spx_open = float(row["spx_open"])
        spx_close = float(row["spx_close"])
        vix_val = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val):
            vix_val = 20.0

        # gates
        if vix_val < VIX_MIN or vix_val >= VIX_MAX:
            continue
        if spx_open <= 0:
            continue
        # Direction filter proxy: green bar
        if spx_close < spx_open:
            continue

        # find target expiry
        if dte == 0:
            expiry = day
        elif dte == 7:
            # "next Friday" semantics — 7 calendar days, must be weekday
            expiry = day + timedelta(days=7)
            while expiry.weekday() >= 5:
                expiry += timedelta(days=1)
        else:
            expiry = next_bday(day, dte)

        day_opts = raw.get(day, {})
        opts = day_opts.get(expiry)
        if opts is None or opts.empty:
            continue

        spread = build_spread(opts, spx_open)
        if spread is None:
            continue

        credit = spread["credit"]
        stop_lvl   = credit * STOP_MULT
        target_lvl = credit * (1.0 - TARGET_PCT)   # mark <= this triggers PT

        # intraday brackets for SL/PT testing
        max_mark = max(spread["short_high"] - spread["long_low"], 0.0)
        min_mark = max(spread["short_low"]  - spread["long_high"], 0.0)

        # exit mark @ 15:45 proxy = close-of-day mark
        eod_mark = max(spread["short_close"] - spread["long_close"], 0.0)

        # Determine exit logic. Same-day "EOD close" rule means we close
        # by 15:45 regardless. Possible triggers: SL (intraday), PT (intraday if PT on), EOD.
        exit_reason = "eod"
        exit_mark = eod_mark
        slip = SLIPPAGE_CLOSE

        sl_hit = max_mark >= stop_lvl
        pt_hit = use_pt and min_mark <= target_lvl

        if sl_hit and pt_hit:
            # Ambiguous order — assume worse (SL fires first, conservative for short side)
            exit_reason = "stop"
            exit_mark = stop_lvl
        elif sl_hit:
            exit_reason = "stop"
            exit_mark = stop_lvl
        elif pt_hit:
            exit_reason = "target"
            exit_mark = target_lvl
        else:
            # closing at 15:45 EOD — note for 0DTE no-PT this is essentially settlement
            # If 0DTE eod_mark is very small, treat as expire (no closing cost)
            if dte == 0 and eod_mark <= 0.05:
                exit_reason = "expire"
                slip = 0.0
            else:
                exit_reason = "eod"

        pnl = (credit - exit_mark - slip) * 100.0
        capture = (credit - exit_mark) / credit if credit > 0 else 0.0

        trades.append(Trade(
            entry_date=day.isoformat(),
            dte=dte,
            use_pt=use_pt,
            short_strike=spread["short_strike"],
            long_strike=spread["long_strike"],
            credit=credit * 100.0,
            exit_mark=exit_mark * 100.0,
            pnl=pnl,
            win=pnl > 0,
            exit_reason=exit_reason,
            vix=vix_val,
            spx_open=spx_open,
            spx_close=spx_close,
            capture_pct=capture * 100.0,
        ))
    return trades


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return dict(n=0)
    pnls = np.array([t.pnl for t in trades])
    n = len(trades)
    wr = (pnls > 0).sum() / n * 100
    total = pnls.sum()
    avg = pnls.mean()
    avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0.0
    avg_loss = pnls[pnls <= 0].mean() if (pnls <= 0).any() else 0.0
    max_loss = pnls.min()
    sh = pnls.mean() / pnls.std() * np.sqrt(252) if pnls.std() > 0 else 0.0
    avg_credit = np.mean([t.credit for t in trades])
    avg_exit = np.mean([t.exit_mark for t in trades])
    avg_capture = np.mean([t.capture_pct for t in trades])
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    yrs = (datetime.fromisoformat(trades[-1].entry_date).date() -
           datetime.fromisoformat(trades[0].entry_date).date()).days / 365.25
    return dict(
        n=n, win_rate=wr, total_pnl=total, avg_pnl=avg,
        avg_win=avg_win, avg_loss=avg_loss, max_loss=max_loss,
        sharpe=sh, avg_credit=avg_credit, avg_exit=avg_exit,
        avg_capture_pct=avg_capture,
        n_stop=reasons.get("stop", 0),
        n_target=reasons.get("target", 0),
        n_eod=reasons.get("eod", 0),
        n_expire=reasons.get("expire", 0),
        years=yrs,
        pnl_per_year=total / yrs if yrs > 0 else total,
    )


def main():
    spx = load_spx_daily()
    spx.index = pd.to_datetime(spx.index).normalize()
    raw = load_all_spxw()
    if not raw:
        print("ERROR: no data")
        return
    start, end = min(raw.keys()), max(raw.keys())
    print(f"\nPeriod {start} → {end}  ({(end-start).days/365.25:.1f} yrs)")
    print(f"OTM={OTM_PCT:.2%} WIDTH={WIDTH} MIN_CR={MIN_CREDIT} SL={STOP_MULT}x PT={TARGET_PCT:.0%} VIX=[{VIX_MIN},{VIX_MAX})")

    results = {}
    for dte in DTE_VARIANTS:
        for use_pt in (False, True):
            label = f"{dte}DTE" + ("_50PT" if use_pt else "_noPT")
            print(f"\n--- {label} ---")
            trades = simulate(raw, spx, dte=dte, use_pt=use_pt)
            s = stats(trades)
            results[label] = s
            print(f"  n={s.get('n',0)}  win%={s.get('win_rate',0):.1f}  "
                  f"total=${s.get('total_pnl',0):,.0f}  "
                  f"avg=${s.get('avg_pnl',0):.1f}  "
                  f"SL={s.get('n_stop',0)} PT={s.get('n_target',0)} EOD={s.get('n_eod',0)} EXP={s.get('n_expire',0)}  "
                  f"cap%={s.get('avg_capture_pct',0):.1f}")
            # write per-variant csv
            csv_path = OUT_DIR / f"trades_{label}.csv"
            with csv_path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()) if trades else ["entry_date"])
                if trades:
                    w.writeheader()
                    for t in trades:
                        w.writerow(asdict(t))
            # year-by-year
            if trades:
                df = pd.DataFrame([asdict(t) for t in trades])
                df["year"] = pd.to_datetime(df["entry_date"]).dt.year
                yearly = df.groupby("year").agg(
                    n=("pnl", "size"),
                    win_rate=("win", lambda x: x.mean() * 100),
                    total=("pnl", "sum"),
                    avg=("pnl", "mean"),
                    avg_credit=("credit", "mean"),
                    avg_capture=("capture_pct", "mean"),
                ).round(2)
                yearly.to_csv(OUT_DIR / f"yearly_{label}.csv")

    # summary json
    with (OUT_DIR / "summary.json").open("w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nWrote results to", OUT_DIR)


if __name__ == "__main__":
    main()
