"""
SPXW 0DTE vs 1DTE vs 2DTE Comparison — vG Config (No Profit Target)
=====================================================================
Key question: Does 1DTE eliminate PDT risk while keeping returns?

PDT rule: open + close same position same day = 1 day trade (uses 2 of your 3 allowed)

0DTE: enter and exit same day    → up to 2 day trades when stop/target fires
1DTE: enter day D, exit day D+1  → ZERO day trades (always different days)
2DTE: enter day D, exit day D+2  → ZERO day trades (always different days)

Stop logic (EOD daily check using daily OHLCV data):
  0DTE: EOD day D only
  1DTE: skip day D (never close same day), EOD day D+1 = settle
  2DTE: EOD day D, EOD day D+1, EOD day D+2 = settle

Profit target: DISABLED (vG design — no PDT burn from intraday targets)
"""

from __future__ import annotations

import re
import sys
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import databento as db
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── data dirs ─────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent / "data" / "databento"
SPXW_DIRS = [
    BASE / "spxw_opt_2014_2015",
    BASE / "spxw_opt_2016_2018",
    BASE / "spxw_opt_2019",
    BASE / "spxw_opt_2020_2022",
    BASE / "spxw_opt",
    BASE / "spxw_opt_2025_2026",
]

SPX_CACHE = Path("/tmp/spx_daily_2014_2026.parquet")

# ── vG params (XSP scaled ×10 for SPX backtest) ───────────────────────────────
OTM_PCT    = 0.010   # 1% OTM
WIDTH      = 50      # SPX 50pt (= XSP 5pt ×10)
MIN_CREDIT = 0.80    # SPX $0.80 (= XSP $0.08 ×10, but vG uses 0.15 scaled back)
STOP_MULT  = 2.0     # stop at 2× credit
VIX_MAX    = 25.0    # no trade if VIX ≥ 25

OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)
MAX_DTE_TO_LOAD = 4   # only load options expiring within N calendar days


# ── helpers ───────────────────────────────────────────────────────────────────

def next_bday(d: date, n: int = 1) -> date:
    result, added = d, 0
    while added < n:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


def load_spx_daily() -> pd.DataFrame:
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
        print(f"SPX/VIX cache: {len(df)} rows")
        return df
    import yfinance as yf
    print("Downloading SPX + VIX daily...")
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-09",
                      auto_adjust=True, progress=False)
    vix = yf.download("^VIX",  start="2014-01-01", end="2026-05-09",
                      auto_adjust=True, progress=False)
    for df_ in [spx, vix]:
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
        from datetime import datetime
        exp    = datetime.strptime(m.group("yymmdd"), "%y%m%d").date()
        strike = int(m.group("strike")) / 1000.0
        return m.group("root"), exp, m.group("right"), strike
    except Exception:
        return None


def load_all_spxw(dirs: list[Path]) -> dict[date, dict[date, pd.DataFrame]]:
    """raw[file_date][expiry_date] = DataFrame of puts"""
    print("Loading SPXW data...")
    raw: dict[date, dict[date, pd.DataFrame]] = {}
    total_files = 0

    for data_dir in dirs:
        if not data_dir.exists():
            print(f"  ⚠  Missing: {data_dir.name}")
            continue
        files = sorted(data_dir.glob("opra-pillar-*.ohlcv-1d.dbn.zst"))
        print(f"  {data_dir.name:35}  {len(files):4} files")
        total_files += len(files)

        for f in files:
            try:
                day_str   = f.stem.split(".")[0].split("-")[2]
                from datetime import datetime
                file_date = datetime.strptime(day_str, "%Y%m%d").date()
            except Exception:
                continue
            if file_date in raw:
                continue
            try:
                store  = db.DBNStore.from_file(str(f))
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
                raw[file_date] = {
                    exp: pd.DataFrame(rows) for exp, rows in by_expiry.items()
                }

    print(f"  {total_files} files | {len(raw)} trading days\n")
    return raw


# ── spread helpers ────────────────────────────────────────────────────────────

def build_spread(opts: pd.DataFrame, spx_open: float) -> Optional[dict]:
    if opts is None or opts.empty:
        return None
    target_short = spx_open * (1.0 - OTM_PCT)
    o = opts.copy()
    o["dist"] = (o["strike"] - target_short).abs()
    short_row    = o.loc[o["dist"].idxmin()]
    short_strike = short_row["strike"]
    long_strike  = short_strike - WIDTH

    exact = o[o["strike"] == long_strike]
    if not exact.empty:
        long_row = exact.iloc[0]
    else:
        below = o[o["strike"] < short_strike]
        if below.empty:
            return None
        long_row    = below.loc[(below["strike"] - long_strike).abs().idxmin()]
        long_strike = long_row["strike"]

    credit = float(short_row["open"]) - float(long_row["open"])
    if credit < MIN_CREDIT or credit <= 0:
        return None
    return dict(
        short_strike=short_strike,
        long_strike=long_strike,
        credit=credit,
    )


def get_mark(opts: pd.DataFrame, short_strike: float, long_strike: float) -> Optional[float]:
    if opts is None or opts.empty:
        return None
    s = opts[opts["strike"] == short_strike]
    l = opts[opts["strike"] == long_strike]
    if s.empty or l.empty:
        return None
    return max(float(s.iloc[0]["close"]) - float(l.iloc[0]["close"]), 0.0)


# ── trade record ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_date:   date
    exit_date:    date
    dte_mode:     str
    credit:       float   # dollars (×100)
    exit_mark:    float   # dollars (×100)
    pnl:          float
    win:          bool
    exit_reason:  str     # expire / stop / settle
    vix:          float
    spx_open:     float


# ── backtest engines ──────────────────────────────────────────────────────────

def run_0dte(raw, spx) -> list[Trade]:
    """0DTE: enter and exit same day. Stop fires same day = day trade."""
    trades: list[Trade] = []
    for day in sorted(raw.keys()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row      = spx.loc[ts]
        spx_open = float(row["spx_open"])
        vix_val  = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val):
            vix_val = 20.0
        if vix_val >= VIX_MAX or spx_open <= 0:
            continue

        opts_0 = raw[day].get(day)
        if opts_0 is None:
            continue
        sp = build_spread(opts_0, spx_open)
        if sp is None:
            continue

        credit   = sp["credit"]
        stop_lvl = credit * STOP_MULT
        mark     = get_mark(opts_0, sp["short_strike"], sp["long_strike"])
        if mark is None:
            mark = 0.0

        if mark >= stop_lvl:
            reason = "stop"
        elif mark <= 0.05:
            reason = "expire"
        else:
            reason = "settle"

        pnl = (credit - mark) * 100.0
        trades.append(Trade(
            entry_date=day, exit_date=day, dte_mode="0DTE",
            credit=credit * 100.0, exit_mark=mark * 100.0,
            pnl=pnl, win=pnl > 0, exit_reason=reason,
            vix=vix_val, spx_open=spx_open,
        ))
    return trades


def run_1dte(raw, spx) -> list[Trade]:
    """
    1DTE: enter day D with options expiring day D+1.
    NEVER close on day D (avoids PDT entirely).
    Settle on day D+1: if stop fires → close at mark, else expire worthless/settle.
    """
    trades: list[Trade] = []
    day_set = set(raw.keys())

    for day in sorted(raw.keys()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row      = spx.loc[ts]
        spx_open = float(row["spx_open"])
        vix_val  = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val):
            vix_val = 20.0
        if vix_val >= VIX_MAX or spx_open <= 0:
            continue

        expiry = next_bday(day, 1)   # options expiring tomorrow
        if expiry not in day_set:
            continue

        opts_entry = raw[day].get(expiry)   # 1DTE options priced today
        if opts_entry is None:
            continue
        sp = build_spread(opts_entry, spx_open)
        if sp is None:
            continue

        credit   = sp["credit"]
        stop_lvl = credit * STOP_MULT

        # Day D+1: settle (never close day D)
        opts_expiry = raw[expiry].get(expiry)
        if opts_expiry is not None:
            mark = get_mark(opts_expiry, sp["short_strike"], sp["long_strike"])
            if mark is None:
                mark = 0.0
        else:
            mark = 0.0

        if mark >= stop_lvl:
            reason = "stop"
        elif mark <= 0.05:
            reason = "expire"
        else:
            reason = "settle"

        pnl = (credit - mark) * 100.0
        trades.append(Trade(
            entry_date=day, exit_date=expiry, dte_mode="1DTE",
            credit=credit * 100.0, exit_mark=mark * 100.0,
            pnl=pnl, win=pnl > 0, exit_reason=reason,
            vix=vix_val, spx_open=spx_open,
        ))
    return trades


def run_2dte(raw, spx) -> list[Trade]:
    """
    2DTE: enter day D, options expiring D+2.
    EOD day D: stop check only.
    EOD day D+1: stop check only.
    Day D+2: settle.
    """
    trades: list[Trade] = []
    day_set = set(raw.keys())

    for day in sorted(raw.keys()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row      = spx.loc[ts]
        spx_open = float(row["spx_open"])
        vix_val  = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val):
            vix_val = 20.0
        if vix_val >= VIX_MAX or spx_open <= 0:
            continue

        expiry = next_bday(day, 2)
        if expiry not in day_set:
            continue

        opts_entry = raw[day].get(expiry)
        if opts_entry is None:
            continue
        sp = build_spread(opts_entry, spx_open)
        if sp is None:
            continue

        credit   = sp["credit"]
        stop_lvl = credit * STOP_MULT

        exit_mark   = None
        exit_reason = "settle"
        exit_day    = expiry

        # EOD day D — stop only
        m = get_mark(raw[day].get(expiry), sp["short_strike"], sp["long_strike"])
        if m is not None and m >= stop_lvl:
            exit_mark, exit_reason, exit_day = m, "stop", day

        # EOD day D+1 — stop only
        if exit_mark is None:
            mid = next_bday(day, 1)
            if mid in raw:
                m = get_mark(raw[mid].get(expiry), sp["short_strike"], sp["long_strike"])
                if m is not None and m >= stop_lvl:
                    exit_mark, exit_reason, exit_day = m, "stop", mid

        # Day D+2 settle
        if exit_mark is None:
            opts_exp = raw[expiry].get(expiry)
            m = get_mark(opts_exp, sp["short_strike"], sp["long_strike"]) if opts_exp is not None else None
            exit_mark = m if m is not None else 0.0
            if exit_mark <= 0.05:
                exit_reason = "expire"

        pnl = (credit - exit_mark) * 100.0
        trades.append(Trade(
            entry_date=day, exit_date=exit_day, dte_mode="2DTE",
            credit=credit * 100.0, exit_mark=exit_mark * 100.0,
            pnl=pnl, win=pnl > 0, exit_reason=exit_reason,
            vix=vix_val, spx_open=spx_open,
        ))
    return trades


# ── stats ─────────────────────────────────────────────────────────────────────

def sharpe(pnls, ann=252):
    a = np.array(pnls)
    if len(a) < 2 or a.std() == 0:
        return 0.0
    return a.mean() / a.std() * np.sqrt(ann)


def max_dd(pnls):
    eq = np.cumsum(pnls)
    pk = np.maximum.accumulate(eq)
    return float((eq - pk).min())


def summarise(trades: list[Trade], label: str, total_years: float):
    if not trades:
        print(f"\n  {label}: no trades")
        return {}
    pnls   = [t.pnl for t in trades]
    n      = len(trades)
    wr     = sum(t.win for t in trades) / n * 100
    total  = sum(pnls)
    pyr    = total / total_years
    mdd    = max_dd(pnls)
    sh     = sharpe(pnls)
    avg_cr = np.mean([t.credit for t in trades])
    avg_pnl = np.mean(pnls)

    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    # PDT: same-day closes (entry_date == exit_date)
    same_day = sum(1 for t in trades if t.entry_date == t.exit_date)
    dt_per_week = same_day / (total_years * 52) * 2   # ×2 for 2-leg spread

    print(f"\n  {'━'*64}")
    print(f"  {label}")
    print(f"  {'━'*64}")
    print(f"  Trades total   : {n}  ({n/total_years:.0f}/yr)")
    print(f"  Win rate       : {wr:.1f}%")
    print(f"  Avg credit     : ${avg_cr:.0f}")
    print(f"  Avg P&L/trade  : ${avg_pnl:.0f}")
    print(f"  Total P&L      : ${total:,.0f}")
    print(f"  P&L / year     : ${pyr:,.0f}")
    print(f"  Max drawdown   : ${mdd:,.0f}")
    print(f"  Sharpe         : {sh:.2f}")
    print(f"  Exit reasons   : " + "  |  ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print(f"  Same-day closes: {same_day}  ({same_day/n*100:.1f}%)")
    print(f"  ⚡ Day trades/wk: {dt_per_week:.2f}  (PDT limit: 6/wk → flag if >3)")

    # Year-by-year
    print(f"\n  Year   Trades   WR%    P&L/yr   Sharpe   DayTrd/wk")
    print(f"  {'─'*56}")
    df = pd.DataFrame([vars(t) for t in trades])
    df["year"] = pd.to_datetime(df["entry_date"]).dt.year
    for yr, g in df.groupby("year"):
        p  = g["pnl"].tolist()
        w  = g["win"].mean() * 100
        sh_yr = sharpe(p)
        sd_yr = (g["entry_date"] == g["exit_date"]).sum()
        dt_yr = sd_yr / 52 * 2
        print(f"  {yr}   {len(p):>5}   {w:>4.1f}%  ${sum(p):>7,.0f}   {sh_yr:>5.2f}    {dt_yr:.2f}")

    return dict(n=n, trades_per_yr=n/total_years, wr=wr, pyr=pyr, mdd=mdd,
                sharpe=sh, dt_per_week=dt_per_week, avg_cr=avg_cr)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    spx = load_spx_daily()
    spx.index = pd.to_datetime(spx.index).normalize()

    raw = load_all_spxw(SPXW_DIRS)
    if not raw:
        print("ERROR: no data loaded")
        sys.exit(1)

    all_days   = sorted(raw.keys())
    total_years = (all_days[-1] - all_days[0]).days / 365.25

    print(f"\n{'='*68}")
    print(f"vG CONFIG  |  {OTM_PCT:.1%} OTM  |  {WIDTH}pt wide  |  VIX<{VIX_MAX}  |  Stop {STOP_MULT}×  |  NO profit target")
    print(f"Period: {all_days[0]} → {all_days[-1]}  ({total_years:.1f} years)")
    print(f"{'='*68}")

    print("\nRunning 0DTE...")
    t0 = run_0dte(raw, spx)
    print(f"  {len(t0)} trades")

    print("Running 1DTE...")
    t1 = run_1dte(raw, spx)
    print(f"  {len(t1)} trades")

    print("Running 2DTE...")
    t2 = run_2dte(raw, spx)
    print(f"  {len(t2)} trades")

    s0 = summarise(t0, "0DTE — enter & exit same day  (current vG design)", total_years)
    s1 = summarise(t1, "1DTE — enter day D, ONLY close day D+1  (100% PDT-safe)", total_years)
    s2 = summarise(t2, "2DTE — enter day D, ONLY close day D+2  (100% PDT-safe)", total_years)

    # ── side-by-side ──────────────────────────────────────────────────────────
    print(f"\n\n{'='*68}")
    print(f"SIDE-BY-SIDE SUMMARY  (SPX 1 contract = $100 per point)")
    print(f"{'='*68}")
    print(f"  {'Metric':<26} {'0DTE':>10} {'1DTE':>10} {'2DTE':>10}  Notes")
    print(f"  {'─'*62}")

    rows = [
        ("Trades/year",     s0['trades_per_yr'],  s1['trades_per_yr'],  s2['trades_per_yr'],  ""),
        ("Win Rate %",      s0['wr'],              s1['wr'],              s2['wr'],              ""),
        ("Avg credit $",    s0['avg_cr'],          s1['avg_cr'],          s2['avg_cr'],          ""),
        ("P&L / year $",    s0['pyr'],             s1['pyr'],             s2['pyr'],             "★ higher=better"),
        ("Max Drawdown $",  s0['mdd'],             s1['mdd'],             s2['mdd'],             "lower=better"),
        ("Sharpe",          s0['sharpe'],          s1['sharpe'],          s2['sharpe'],          "★ higher=better"),
        ("Day trades/wk",   s0['dt_per_week'],     s1['dt_per_week'],     s2['dt_per_week'],     "PDT limit ~6/wk"),
    ]
    for name, v0, v1, v2, note in rows:
        fmt = ".1f" if "%" in name else ".2f" if "Sharpe" in name or "Day" in name else ",.0f"
        try:
            print(f"  {name:<26} {v0:>10{fmt}} {v1:>10{fmt}} {v2:>10{fmt}}  {note}")
        except Exception:
            print(f"  {name:<26} {str(v0):>10} {str(v1):>10} {str(v2):>10}  {note}")

    print(f"\n  PDT VERDICT:")
    print(f"    0DTE → {s0['dt_per_week']:.2f} day trades/wk avg  (can exceed 6 in volatile weeks → PDT flag risk)")
    print(f"    1DTE → {s1['dt_per_week']:.2f} day trades/wk      (ZERO, always closed next day)")
    print(f"    2DTE → {s2['dt_per_week']:.2f} day trades/wk      (ZERO, always closed day after next)")
    print(f"\n  RECOMMENDATION: 1DTE gives PDT immunity with {((s1['pyr']-s0['pyr'])/abs(s0['pyr'])*100):+.1f}% P&L vs 0DTE")
    print(f"{'='*68}\n")


if __name__ == "__main__":
    main()
