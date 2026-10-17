"""
SPXW 0DTE vs 2DTE Comparison — vG Config (1% OTM, 50pt, VIX<25, direction filter)
===================================================================================
Answers: Does switching to 2DTE-only help PDT without hurting returns?

0DTE: enter and exit same day → up to 2 day trades if closed early
2DTE: enter day D, exit day D+2 → ZERO day trades (different days always)
Mixed: prefer 2DTE, fall back to 0DTE (mirrors dte_candidates: [0, 2] reversed)

Monitoring for 2DTE:
  - EOD day D:   check stop  (mark > 2x credit → close at mark)
  - EOD day D+1: check stop/target
  - EOD day D+2: settlement (close at mark price)
"""

from __future__ import annotations

import re
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

# ── vG params ─────────────────────────────────────────────────────────────────
OTM_PCT    = 0.010
WIDTH      = 50
MIN_CREDIT = 0.80
STOP_MULT  = 2.0
TARGET_PCT = 0.50
VIX_MAX    = 25.0

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")

MAX_DTE_TO_LOAD = 4   # only load options expiring within 4 calendar days


# ── helpers ───────────────────────────────────────────────────────────────────

def next_bday(d: date, n: int = 1) -> date:
    """Return the nth business day after d."""
    result = d
    added = 0
    while added < n:
        result += timedelta(days=1)
        if result.weekday() < 5:   # Mon-Fri
            added += 1
    return result


def load_spx_daily() -> pd.DataFrame:
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
        print(f"SPX/VIX cache loaded: {len(df)} rows")
        return df
    import yfinance as yf
    print("Downloading SPX + VIX daily from yfinance...")
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-09", auto_adjust=True, progress=False)
    vix = yf.download("^VIX",  start="2014-01-01", end="2026-05-09", auto_adjust=True, progress=False)
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
    """
    Returns: raw[file_date][expiry_date] = DataFrame of puts
    Only keeps options expiring within MAX_DTE_TO_LOAD days of file_date.
    """
    print("Loading SPXW data (all near-term expiries)...")
    raw: dict[date, dict[date, pd.DataFrame]] = {}
    total_files = 0

    for data_dir in dirs:
        if not data_dir.exists():
            print(f"  ⚠️  Missing: {data_dir.name}")
            continue
        files = sorted(data_dir.glob("opra-pillar-*.ohlcv-1d.dbn.zst"))
        print(f"  {data_dir.name:30}  {len(files):4} files")
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
                # Only load options expiring within MAX_DTE_TO_LOAD calendar days
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

    print(f"  Loaded {total_files} files | {len(raw)} trading days with options\n")
    return raw


# ── spread builder ────────────────────────────────────────────────────────────

def build_spread(opts: pd.DataFrame, spx_open: float) -> Optional[dict]:
    """Find best short strike near OTM_PCT below spx_open, return spread info."""
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
        short_open=float(short_row["open"]),
        long_open=float(long_row["open"]),
    )


def get_mark(opts: pd.DataFrame, short_strike: float, long_strike: float) -> Optional[float]:
    """Get EOD mark of spread from options DataFrame."""
    if opts is None or opts.empty:
        return None
    s = opts[opts["strike"] == short_strike]
    l = opts[opts["strike"] == long_strike]
    if s.empty or l.empty:
        return None
    return max(float(s.iloc[0]["close"]) - float(l.iloc[0]["close"]), 0.0)


# ── trade dataclass ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_date:   date
    exit_date:    date
    dte_mode:     str    # "0DTE" or "2DTE"
    short_strike: float
    long_strike:  float
    credit:       float  # dollars (×100)
    exit_mark:    float
    pnl:          float
    win:          bool
    exit_reason:  str
    vix:          float
    spx_open:     float
    spx_close:    float  # close on entry day


# ── backtest engines ──────────────────────────────────────────────────────────

def run_0dte(
    raw: dict[date, dict[date, pd.DataFrame]],
    spx: pd.DataFrame,
) -> list[Trade]:
    """0DTE only — enter and exit same day."""
    trades: list[Trade] = []
    for day in sorted(raw.keys()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row      = spx.loc[ts]
        spx_open = float(row["spx_open"])
        vix_val  = float(row.get("vix_close", 20.0))

        if pd.isna(vix_val): vix_val = 20.0
        if vix_val >= VIX_MAX:
            continue
        if spx_open <= 0:
            continue
        # direction filter: only enter if SPX closed above open
        spx_close = float(row["spx_close"])
        # Note: 0DTE uses SPX close to evaluate direction, but entry is at open

        day_opts = raw[day]
        opts_0   = day_opts.get(day)  # options expiring today
        if opts_0 is None:
            continue

        spread = build_spread(opts_0, spx_open)
        if spread is None:
            continue

        credit = spread["credit"]
        mark   = get_mark(opts_0, spread["short_strike"], spread["long_strike"])
        if mark is None:
            mark = 0.0

        stop_lvl   = credit * STOP_MULT
        target_lvl = credit * (1.0 - TARGET_PCT)

        if mark <= 0.05:
            reason = "expire"
        elif mark <= target_lvl:
            reason = "target"
        elif mark >= stop_lvl:
            reason = "stop"
        else:
            reason = "close"

        pnl = (credit - mark) * 100.0

        trades.append(Trade(
            entry_date=day, exit_date=day, dte_mode="0DTE",
            short_strike=spread["short_strike"], long_strike=spread["long_strike"],
            credit=credit * 100.0, exit_mark=mark * 100.0,
            pnl=pnl, win=pnl > 0,
            exit_reason=reason,
            vix=vix_val, spx_open=spx_open, spx_close=spx_close,
        ))
    return trades


def run_2dte(
    raw: dict[date, dict[date, pd.DataFrame]],
    spx: pd.DataFrame,
) -> list[Trade]:
    """2DTE only — enter day D, exit day D+2 business days."""
    trades: list[Trade] = []
    all_days = sorted(raw.keys())
    day_set  = set(all_days)

    for day in all_days:
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row      = spx.loc[ts]
        spx_open = float(row["spx_open"])
        vix_val  = float(row.get("vix_close", 20.0))
        if pd.isna(vix_val): vix_val = 20.0
        if vix_val >= VIX_MAX:
            continue
        if spx_open <= 0:
            continue
        spx_close = float(row["spx_close"])

        # Find expiry = 2 business days from today
        expiry = next_bday(day, 2)
        if expiry not in day_set:
            continue   # no data for expiry day

        day_opts = raw[day]
        opts_entry = day_opts.get(expiry)   # options expiring in 2bd, priced today
        if opts_entry is None:
            continue

        spread = build_spread(opts_entry, spx_open)
        if spread is None:
            continue

        credit     = spread["credit"]
        stop_lvl   = credit * STOP_MULT
        target_lvl = credit * (1.0 - TARGET_PCT)

        # Intermediate day = 1 business day from entry
        mid_day    = next_bday(day, 1)

        exit_mark   = None
        exit_reason = "close"

        # EOD day D — check stop only
        eod_d_opts = raw[day].get(expiry)
        if eod_d_opts is not None:
            m = get_mark(eod_d_opts, spread["short_strike"], spread["long_strike"])
            if m is not None and m >= stop_lvl:
                exit_mark   = m
                exit_reason = "stop"
                exit_day    = day

        # EOD day D+1 — check stop/target (if not already stopped)
        if exit_mark is None and mid_day in raw:
            eod_mid_opts = raw[mid_day].get(expiry)
            if eod_mid_opts is not None:
                m = get_mark(eod_mid_opts, spread["short_strike"], spread["long_strike"])
                if m is not None:
                    if m >= stop_lvl:
                        exit_mark   = m
                        exit_reason = "stop"
                        exit_day    = mid_day
                    elif m <= target_lvl:
                        exit_mark   = m
                        exit_reason = "target"
                        exit_day    = mid_day

        # EOD day D+2 (expiry) — final settlement
        if exit_mark is None:
            expiry_opts = raw[expiry].get(expiry)
            if expiry_opts is not None:
                m = get_mark(expiry_opts, spread["short_strike"], spread["long_strike"])
                exit_mark = m if m is not None else 0.0
            else:
                exit_mark = 0.0
            if exit_mark <= 0.05:
                exit_reason = "expire"
            elif exit_mark <= target_lvl:
                exit_reason = "target"
            elif exit_mark >= stop_lvl:
                exit_reason = "stop"
            exit_day = expiry

        pnl = (credit - exit_mark) * 100.0

        trades.append(Trade(
            entry_date=day, exit_date=exit_day, dte_mode="2DTE",
            short_strike=spread["short_strike"], long_strike=spread["long_strike"],
            credit=credit * 100.0, exit_mark=exit_mark * 100.0,
            pnl=pnl, win=pnl > 0,
            exit_reason=exit_reason,
            vix=vix_val, spx_open=spx_open, spx_close=spx_close,
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


def summarise(trades: list[Trade], label: str):
    if not trades:
        print(f"  {label}: no trades")
        return
    pnls   = [t.pnl for t in trades]
    n      = len(trades)
    wr     = sum(t.win for t in trades) / n * 100
    avg    = np.mean(pnls)
    total  = sum(pnls)
    years  = (trades[-1].entry_date - trades[0].entry_date).days / 365.25
    pyr    = total / years if years > 0 else total
    mdd    = max_dd(pnls)
    sh     = sharpe(pnls)
    avg_cr = np.mean([t.credit for t in trades])

    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    print(f"\n  ── {label} {'─'*(60-len(label))}")
    print(f"     Trades : {n}")
    print(f"     Win Rate: {wr:.1f}%")
    print(f"     Avg Credit: ${avg_cr:.0f}")
    print(f"     Avg P&L/trade: ${avg:.0f}")
    print(f"     Total P&L: ${total:,.0f}")
    print(f"     P&L/year: ${pyr:,.0f}")
    print(f"     Max Drawdown: ${mdd:,.0f}")
    print(f"     Sharpe: {sh:.2f}")
    print(f"     Exit reasons: " + " | ".join(f"{k}={v}" for k, v in sorted(reasons.items())))

    # PDT analysis
    same_day_closes = sum(1 for t in trades if t.entry_date == t.exit_date and t.exit_reason in ("target", "stop", "close"))
    pct_same = same_day_closes / n * 100
    print(f"     Same-day closes (day trades): {same_day_closes}/{n} = {pct_same:.1f}%")
    print(f"     ⚡ Day trades per week (est): {same_day_closes / (years * 52):.1f}/week × 2 legs = {same_day_closes / (years * 52) * 2:.1f} day trades/week")

    # Year by year
    print(f"\n     Year  Trades   WR%   $/yr    Sharpe")
    print(f"     {'─'*45}")
    df = pd.DataFrame([vars(t) for t in trades])
    df["year"] = pd.to_datetime(df["entry_date"]).dt.year
    for yr, g in df.groupby("year"):
        p  = g["pnl"].tolist()
        w  = g["win"].mean() * 100
        sh = sharpe(p)
        print(f"     {yr}   {len(p):>5}   {w:>4.1f}%  ${sum(p):>7,.0f}   {sh:>5.2f}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    spx = load_spx_daily()
    spx.index = pd.to_datetime(spx.index).normalize()

    raw = load_all_spxw(SPXW_DIRS)
    if not raw:
        print("ERROR: no data loaded")
        return

    start = min(raw.keys())
    end   = max(raw.keys())
    years = (end - start).days / 365.25

    print(f"\n{'='*70}")
    print(f"vG CONFIG: {OTM_PCT:.1%} OTM | {WIDTH}pt wide | VIX<{VIX_MAX} | Stop {STOP_MULT}x | Target {TARGET_PCT:.0%}")
    print(f"Period: {start} → {end} ({years:.1f} years)")
    print(f"{'='*70}")
    print(f"\nRunning 0DTE backtest...")
    trades_0 = run_0dte(raw, spx)
    print(f"  → {len(trades_0)} trades")

    print(f"\nRunning 2DTE backtest...")
    trades_2 = run_2dte(raw, spx)
    print(f"  → {len(trades_2)} trades")

    print(f"\n\n{'='*70}")
    print(f"RESULTS COMPARISON")
    print(f"{'='*70}")
    summarise(trades_0, "0DTE (current vG)")
    summarise(trades_2, "2DTE only (PDT-safe)")

    # Side-by-side summary
    def quick_stats(tl):
        if not tl: return {}
        pnls  = [t.pnl for t in tl]
        n     = len(tl)
        years = (tl[-1].entry_date - tl[0].entry_date).days / 365.25
        return dict(
            n=n, wr=sum(t.win for t in tl)/n*100,
            pyr=sum(pnls)/years, mdd=max_dd(pnls),
            sharpe=sharpe(pnls),
        )

    s0 = quick_stats(trades_0)
    s2 = quick_stats(trades_2)

    print(f"\n\n{'='*70}")
    print(f"SIDE-BY-SIDE SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Metric':<22} {'0DTE':>12} {'2DTE':>12}  {'Better':>8}")
    print(f"  {'─'*56}")
    metrics = [
        ("Trades/yr",   s0['n']/years,    s2['n']/years,    "higher"),
        ("Win Rate %",  s0['wr'],          s2['wr'],          "higher"),
        ("P&L/year $",  s0['pyr'],         s2['pyr'],         "higher"),
        ("Max DD $",    s0['mdd'],         s2['mdd'],         "lower"),
        ("Sharpe",      s0['sharpe'],      s2['sharpe'],      "higher"),
    ]
    for name, v0, v2, better in metrics:
        winner = "0DTE" if (v0 > v2) == (better == "higher") else "2DTE"
        if name == "Max DD $": winner = "0DTE" if v0 > v2 else "2DTE"
        print(f"  {name:<22} {v0:>12.1f} {v2:>12.1f}  {winner:>8}")

    print(f"\n  PDT day trades/week (est):")
    sd0 = sum(1 for t in trades_0 if t.entry_date == t.exit_date and t.exit_reason in ("target","stop","close"))
    sd2 = sum(1 for t in trades_2 if t.entry_date == t.exit_date and t.exit_reason in ("target","stop","close"))
    print(f"    0DTE: {sd0/(years*52)*2:.2f} day trades/week avg")
    print(f"    2DTE: {sd2/(years*52)*2:.2f} day trades/week avg  ← PDT safe")
    print(f"\n  PDT limit: 3 day trades per 5 rolling days")
    print(f"  2DTE same-day closes only happen if stop fires on entry day itself (rare)")


if __name__ == "__main__":
    main()
