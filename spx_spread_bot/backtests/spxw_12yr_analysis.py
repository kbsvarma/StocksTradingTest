"""
SPXW 0DTE BPS — 12-Year Full Analysis (2014–2026)
==================================================
Uses all acquired Databento data across:
  spxw_opt_2014_2015 / spxw_opt_2016_2018 / spxw_opt_2019
  spxw_opt_2020_2022 / spxw_opt (2023-2024) / spxw_opt_2025_2026

Runs the same engine as spxw_deep_analysis.py with:
  - 12-year summary table (all configs)
  - Year-by-year P&L
  - Month-of-year seasonality
  - VIX regime breakdown (with bear market data)
  - Day-of-week
  - SPX intraday move buckets
  - Credit distribution
  - VIX-filtered strategy (only trade when VIX < 20)
  - Best config deep dive
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import databento as db
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# ── data dirs — all SPXW year ranges ─────────────────────────────────────────
BASE = Path(__file__).parent.parent / "data" / "databento"
SPXW_DIRS = [
    BASE / "spxw_opt_2014_2015",   # 2014-01-01 → 2015-12-31
    BASE / "spxw_opt_2016_2018",   # 2016-01-01 → 2018-12-31
    BASE / "spxw_opt_2019",        # 2019-01-01 → 2019-12-31
    BASE / "spxw_opt_2020_2022",   # 2020-01-01 → 2022-12-31
    BASE / "spxw_opt",             # 2023-01-01 → 2024-12-31
    BASE / "spxw_opt_2025_2026",   # 2025-01-01 → 2026-05-08
]

SPX_CACHE = Path("/tmp/spx_daily_2014_2026.parquet")

# ── strategy params ────────────────────────────────────────────────────────────
MIN_CREDIT = 0.80
STOP_MULT  = 2.0
TARGET_PCT = 0.50

CONFIGS = [
    dict(otm_pct=0.005, width=25),
    dict(otm_pct=0.005, width=50),
    dict(otm_pct=0.010, width=25),
    dict(otm_pct=0.010, width=50),
    dict(otm_pct=0.015, width=25),
    dict(otm_pct=0.015, width=50),
]
FOCUS_CFG = dict(otm_pct=0.010, width=50)

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


# ── data loaders ──────────────────────────────────────────────────────────────

def load_spx_daily() -> pd.DataFrame:
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
        print(f"SPX/VIX cache loaded: {len(df)} rows ({df.index[0].date()} → {df.index[-1].date()})")
        return df
    print("Downloading SPX + VIX daily 2014-2026 from yfinance...")
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-09", auto_adjust=True, progress=False)
    vix = yf.download("^VIX",  start="2014-01-01", end="2026-05-09", auto_adjust=True, progress=False)
    if isinstance(spx.columns, pd.MultiIndex):
        spx.columns = [c[0] for c in spx.columns]
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = [c[0] for c in vix.columns]
    df = spx[["Open", "High", "Low", "Close"]].copy()
    df.columns = ["spx_open", "spx_high", "spx_low", "spx_close"]
    df["vix_close"] = vix["Close"]
    df.index = pd.to_datetime(df.index).normalize()
    df.dropna(subset=["spx_open"], inplace=True)
    df.to_parquet(SPX_CACHE)
    print(f"SPX daily saved: {len(df)} rows")
    return df


def parse_symbol(sym: str):
    m = OCC_RE.match(sym.strip())
    if not m:
        return None
    try:
        exp    = datetime.strptime(m.group("yymmdd"), "%y%m%d").date()
        strike = int(m.group("strike")) / 1000.0
        return m.group("root"), exp, m.group("right"), strike
    except Exception:
        return None


def load_spxw_daily(dirs: list[Path]) -> dict[date, pd.DataFrame]:
    """Load 0DTE SPXW puts from all data directories into a single date→DataFrame map."""
    print("Loading SPXW daily prices from all year ranges...")
    day_map: dict[date, list[dict]] = {}
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
                file_date = datetime.strptime(day_str, "%Y%m%d").date()
            except Exception:
                continue

            if file_date in day_map:
                continue  # duplicate across dirs

            try:
                store  = db.DBNStore.from_file(str(f))
                df_raw = store.to_df()
            except Exception:
                continue

            rows = []
            for sym, grp in df_raw.groupby("symbol"):
                parsed = parse_symbol(str(sym).strip())
                if not parsed:
                    continue
                root, exp, right, strike = parsed
                if root not in ("SPXW", "SPX"):
                    continue
                if right != "P":
                    continue
                if exp != file_date:
                    continue
                r = grp.iloc[0]
                rows.append({
                    "strike": strike,
                    "open":   float(r["open"]),
                    "high":   float(r["high"]),
                    "low":    float(r["low"]),
                    "close":  float(r["close"]),
                    "volume": int(r["volume"]),
                })

            if rows:
                day_map[file_date] = pd.DataFrame(rows)

    total_days = len(day_map)
    total_rows = sum(len(v) for v in day_map.values())
    years = sorted(set(d.year for d in day_map))
    print(f"\n  Total: {total_files} files | {total_days} trading days | {total_rows:,} option rows")
    print(f"  Years covered: {years[0]}–{years[-1]}")
    return day_map


# ── backtest engine ────────────────────────────────────────────────────────────

@dataclass
class Trade:
    date:         date
    otm_pct:      float
    width:        int
    short_strike: float
    long_strike:  float
    credit:       float   # in dollars (×100)
    exit_mark:    float
    pnl:          float
    win:          bool
    exit_reason:  str
    spx_open:     float
    spx_close:    float
    vix:          float
    direction_ok: bool


def run_backtest(
    day_map: dict[date, pd.DataFrame],
    spx: pd.DataFrame,
    otm_pct: float,
    width: int,
    direction_filter: bool = False,
    vix_filter: Optional[float] = None,   # skip days where VIX >= this
) -> list[Trade]:
    trades: list[Trade] = []

    for day, opts in sorted(day_map.items()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        row = spx.loc[ts]
        spx_open  = float(row["spx_open"])
        spx_close = float(row["spx_close"])
        vix_val   = float(row["vix_close"]) if not pd.isna(row.get("vix_close", float("nan"))) else 20.0
        dir_ok    = spx_close >= spx_open

        if direction_filter and not dir_ok:
            continue
        if vix_filter is not None and vix_val >= vix_filter:
            continue

        target_short = spx_open * (1.0 - otm_pct)
        opts = opts.copy()
        opts["dist"] = (opts["strike"] - target_short).abs()
        short_row = opts.loc[opts["dist"].idxmin()]
        short_strike = short_row["strike"]
        long_strike  = short_strike - width

        exact = opts[opts["strike"] == long_strike]
        if not exact.empty:
            long_row = exact.iloc[0]
        else:
            below = opts[opts["strike"] < short_strike]
            if below.empty:
                continue
            long_row     = below.loc[(below["strike"] - long_strike).abs().idxmin()]
            long_strike  = long_row["strike"]

        credit = float(short_row["open"]) - float(long_row["open"])
        if credit < MIN_CREDIT or credit <= 0:
            continue

        mark = max(float(short_row["close"]) - float(long_row["close"]), 0.0)
        stop_lvl   = credit * STOP_MULT
        target_lvl = credit * (1.0 - TARGET_PCT)

        if mark <= 0.05:
            pnl = credit;  reason = "expire"
        elif mark <= target_lvl:
            pnl = credit - mark;  reason = "target"
        elif mark >= stop_lvl:
            pnl = credit - mark;  reason = "stop"
        else:
            pnl = credit - mark;  reason = "close"

        MULT = 100.0
        trades.append(Trade(
            date=day, otm_pct=otm_pct, width=width,
            short_strike=short_strike, long_strike=long_strike,
            credit=credit * MULT, exit_mark=mark * MULT,
            pnl=pnl * MULT, win=pnl > 0,
            exit_reason=reason,
            spx_open=spx_open, spx_close=spx_close,
            vix=vix_val, direction_ok=dir_ok,
        ))
    return trades


# ── stats ──────────────────────────────────────────────────────────────────────

def sharpe(pnls, ann=252):
    a = np.array(pnls)
    if len(a) < 2 or a.std() == 0:
        return 0.0
    return a.mean() / a.std() * np.sqrt(ann)


def max_dd(pnls):
    eq = np.cumsum(pnls)
    pk = np.maximum.accumulate(eq)
    return float((eq - pk).min())


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return {}
    pnls  = [t.pnl for t in trades]
    n     = len(trades)
    wr    = sum(t.win for t in trades) / n * 100
    avg   = np.mean(pnls)
    total = sum(pnls)
    years = (trades[-1].date - trades[0].date).days / 365.25
    pyr   = total / years if years > 0 else total
    mdd   = max_dd(pnls)
    sh    = sharpe(pnls)
    return dict(n=n, wr=wr, avg=avg, total=total, pyr=pyr, maxdd=mdd, sharpe=sh)


def vix_regime(v):
    if v < 15:  return "Low   <15"
    if v < 20:  return "Mid 15-20"
    if v < 30:  return "High 20-30"
    return "Spike  >30"


def M(v):  return f"${v:>9.0f}"


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    spx = load_spx_daily()
    spx.index = pd.to_datetime(spx.index).normalize()

    day_map = load_spxw_daily(SPXW_DIRS)
    if not day_map:
        print("ERROR: no data loaded")
        return

    years_in_data = sorted(set(d.year for d in day_map))
    start_date = min(day_map.keys())
    end_date   = max(day_map.keys())
    n_years    = (end_date - start_date).days / 365.25

    # ── SECTION 1: 12-year summary sweep ─────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"SPXW 0DTE BPS — 12-YEAR FULL ANALYSIS | {start_date} → {end_date} | {n_years:.1f} years")
    print(f"{'='*90}")

    results: dict[str, dict] = {}
    for cfg in CONFIGS:
        otm, w = cfg["otm_pct"], cfg["width"]
        key = f"{otm:.1%}_{w}pt"
        t_nofilter = run_backtest(day_map, spx, otm, w, direction_filter=False)
        t_dirfilt  = run_backtest(day_map, spx, otm, w, direction_filter=True)
        t_vixfilt  = run_backtest(day_map, spx, otm, w, vix_filter=20.0)
        results[key] = dict(nofilter=t_nofilter, dirfilt=t_dirfilt, vixfilt=t_vixfilt)

    for label, mode in [
        ("No Filter",        "nofilter"),
        ("Direction Filter", "dirfilt"),
        ("VIX < 20 Filter",  "vixfilt"),
    ]:
        print(f"\n── {label} {'─'*(72-len(label))}")
        print(f"  {'OTM%':>6} {'Width':>6} {'Trades':>7} {'WR%':>6} {'Avg$/tr':>8} {'$/yr':>9} {'Total$':>9} {'MaxDD':>8} {'Sharpe':>7}")
        print("  " + "─"*75)
        for cfg in CONFIGS:
            otm, w = cfg["otm_pct"], cfg["width"]
            key = f"{otm:.1%}_{w}pt"
            s = stats(results[key][mode])
            if not s:
                print(f"  {otm:.1%}  {w:>5}pt  —")
                continue
            print(f"  {otm:.1%}  {w:>5}pt  {s['n']:>7}  {s['wr']:>5.1f}%  {M(s['avg'])}  {M(s['pyr'])}  {M(s['total'])}  {M(s['maxdd'])}  {s['sharpe']:>6.2f}")

    print(f"\n  Min credit: ${MIN_CREDIT:.2f} | Stop: {STOP_MULT}x EOD | Target: {TARGET_PCT:.0%} EOD | $100 multiplier")

    # ── SECTION 2: Best config deep dive (1% OTM, 50pt, no filter) ───────────
    focus_key   = f"{FOCUS_CFG['otm_pct']:.1%}_{FOCUS_CFG['width']}pt"
    focus_label = f"OTM {FOCUS_CFG['otm_pct']:.1%} | Width {FOCUS_CFG['width']}pt"
    trades      = results[focus_key]["nofilter"]

    print(f"\n\n{'='*90}")
    print(f"DEEP DIVE: {focus_label} — No Filter | {start_date} → {end_date}")
    print(f"{'='*90}")

    if not trades:
        print("  No trades.")
        return

    df = pd.DataFrame([vars(t) for t in trades])
    df["date"]   = pd.to_datetime(df["date"])
    df["year"]   = df["date"].dt.year
    df["month"]  = df["date"].dt.month
    df["mon_yr"] = df["date"].dt.to_period("M").astype(str)
    df["dow"]    = df["date"].dt.day_name()
    df["qtr"]    = df["date"].dt.to_period("Q").astype(str)
    df["regime"] = df["vix"].apply(vix_regime)
    df["spx_chg"]= (df["spx_close"] - df["spx_open"]) / df["spx_open"] * 100

    # 2a Year-by-Year
    print(f"\n── Year-by-Year ─────────────────────────────────────────────────────────────")
    print(f"  {'Year':>5} {'Days':>5} {'WR%':>6} {'Total$':>9} {'$/yr':>9} {'MaxDD':>8} {'Sharpe':>7}")
    print("  " + "─"*60)
    for yr in years_in_data:
        ydf  = df[df["year"] == yr]
        if ydf.empty:
            continue
        pnls = ydf["pnl"].tolist()
        wr   = ydf["win"].mean() * 100
        sh   = sharpe(pnls)
        print(f"  {yr:>5}  {len(pnls):>4}  {wr:>5.1f}%  {M(sum(pnls))}  {M(sum(pnls))}  {M(max_dd(pnls))}  {sh:>6.2f}")

    # 2b Month seasonality (all years combined)
    print(f"\n── Month-of-Year Seasonality (all years combined) ───────────────────────────")
    mon_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                 7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    print(f"  {'Month':>5} {'Obs':>5} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9}")
    print("  " + "─"*40)
    for m in range(1, 13):
        mdf = df[df["month"] == m]
        if mdf.empty:
            continue
        wr  = mdf["win"].mean() * 100
        avg = mdf["pnl"].mean()
        tot = mdf["pnl"].sum()
        print(f"  {mon_names[m]:>5}  {len(mdf):>4}  {wr:>5.1f}%  {M(avg)}  {M(tot)}")

    # 2c VIX regime — the key table
    print(f"\n── VIX Regime Breakdown ─────────────────────────────────────────────────────")
    print(f"  {'Regime':>12} {'Trades':>7} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9} {'MaxDD':>8} {'Sharpe':>7}")
    print("  " + "─"*68)
    for reg in ["Low   <15", "Mid 15-20", "High 20-30", "Spike  >30"]:
        rdf = df[df["regime"] == reg]
        if rdf.empty:
            continue
        pnls = rdf["pnl"].tolist()
        wr   = rdf["win"].mean() * 100
        sh   = sharpe(pnls)
        print(f"  {reg:>12}  {len(pnls):>6}  {wr:>5.1f}%  {M(np.mean(pnls))}  {M(sum(pnls))}  {M(max_dd(pnls))}  {sh:>6.2f}")

    # 2d VIX filter comparison
    print(f"\n── VIX Filter Variants — 1% OTM / 50pt ─────────────────────────────────────")
    print(f"  {'Filter':>18} {'Trades':>7} {'WR%':>6} {'$/yr':>9} {'Total$':>9} {'MaxDD':>8} {'Sharpe':>7}")
    print("  " + "─"*72)
    for label_s, mode in [
        ("No filter",       "nofilter"),
        ("VIX < 20",        "vixfilt"),
        ("Direction bull",  "dirfilt"),
    ]:
        s = stats(results[focus_key][mode])
        if not s:
            continue
        print(f"  {label_s:>18}  {s['n']:>6}  {s['wr']:>5.1f}%  {M(s['pyr'])}  {M(s['total'])}  {M(s['maxdd'])}  {s['sharpe']:>6.2f}")

    # Also: VIX < 25 variant
    t_vix25 = run_backtest(day_map, spx, FOCUS_CFG["otm_pct"], FOCUS_CFG["width"], vix_filter=25.0)
    s25 = stats(t_vix25)
    if s25:
        print(f"  {'VIX < 25':>18}  {s25['n']:>6}  {s25['wr']:>5.1f}%  {M(s25['pyr'])}  {M(s25['total'])}  {M(s25['maxdd'])}  {s25['sharpe']:>6.2f}")

    # 2e Day-of-Week
    print(f"\n── Day-of-Week ──────────────────────────────────────────────────────────────")
    print(f"  {'Day':>10} {'Trades':>7} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9}")
    print("  " + "─"*48)
    for day in ["Monday","Tuesday","Wednesday","Thursday","Friday"]:
        ddf = df[df["dow"] == day]
        if ddf.empty:
            continue
        wr  = ddf["win"].mean() * 100
        avg = ddf["pnl"].mean()
        tot = ddf["pnl"].sum()
        print(f"  {day:>10}  {len(ddf):>6}  {wr:>5.1f}%  {M(avg)}  {M(tot)}")

    # 2f SPX move buckets
    print(f"\n── SPX Intraday Move vs BPS Result (all trades) ────────────────────────────")
    print(f"  {'SPX Move':>18} {'Trades':>7} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9}")
    print("  " + "─"*56)
    move_bins = [
        (-99, -2.0,  "< -2.0%"),
        (-2.0, -1.0, "-2.0% to -1.0%"),
        (-1.0, -0.5, "-1.0% to -0.5%"),
        (-0.5,  0.0, "-0.5% to  0.0%"),
        ( 0.0,  0.5,  "0.0% to +0.5%"),
        ( 0.5,  1.0, "+0.5% to +1.0%"),
        ( 1.0,  2.0, "+1.0% to +2.0%"),
        ( 2.0,  99,  "> +2.0%"),
    ]
    for lo, hi, lab in move_bins:
        bdf = df[(df["spx_chg"] > lo) & (df["spx_chg"] <= hi)]
        if bdf.empty:
            continue
        wr  = bdf["win"].mean() * 100
        avg = bdf["pnl"].mean()
        tot = bdf["pnl"].sum()
        print(f"  {lab:>18}  {len(bdf):>6}  {wr:>5.1f}%  {M(avg)}  {M(tot)}")

    # 2g Exit reason
    print(f"\n── Exit Reason Breakdown ────────────────────────────────────────────────────")
    print(f"  {'Reason':>8} {'Count':>7} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9}")
    print("  " + "─"*48)
    for reason in ["expire", "target", "stop", "close"]:
        edf = df[df["exit_reason"] == reason]
        if edf.empty:
            continue
        wr  = edf["win"].mean() * 100
        avg = edf["pnl"].mean()
        tot = edf["pnl"].sum()
        print(f"  {reason:>8}  {len(edf):>6}  {wr:>5.1f}%  {M(avg)}  {M(tot)}")

    # 2h Credit distribution
    print(f"\n── Credit Distribution ──────────────────────────────────────────────────────")
    bins   = [0.80, 1.0, 1.5, 2.0, 3.0, 5.0, 999.0]
    labels = ["$0.80-1.00","$1.00-1.50","$1.50-2.00","$2.00-3.00","$3.00-5.00",">$5.00"]
    print(f"  {'Credit':>12} {'Trades':>7} {'WR%':>6} {'Avg$/tr':>8} {'Total$':>9}")
    print("  " + "─"*52)
    for lo, hi, lab in zip(bins, bins[1:], labels):
        # credit column is in dollars (already ×100), bins in option price
        bdf = df[(df["credit"]/100 >= lo) & (df["credit"]/100 < hi)]
        if bdf.empty:
            continue
        wr  = bdf["win"].mean() * 100
        avg = bdf["pnl"].mean()
        tot = bdf["pnl"].sum()
        print(f"  {lab:>12}  {len(bdf):>6}  {wr:>5.1f}%  {M(avg)}  {M(tot)}")

    # 2i Consecutive loss streaks
    print(f"\n── Streak Analysis ──────────────────────────────────────────────────────────")
    wins = df["win"].tolist()
    max_win_streak = max_loss_streak = cur = 0
    cur_type = None
    loss_streaks = []
    for w in wins:
        if w:
            if cur_type == "L":
                loss_streaks.append(cur)
            cur_type = "W"
            cur = (cur + 1) if cur_type == "W" else 1
            max_win_streak = max(max_win_streak, cur)
        else:
            if cur_type == "W":
                cur = 1
            else:
                cur = (cur + 1) if cur_type == "L" else 1
            cur_type = "L"
            max_loss_streak = max(max_loss_streak, cur)
    if cur_type == "L":
        loss_streaks.append(cur)
    print(f"  Max win streak:   {max_win_streak}")
    print(f"  Max loss streak:  {max_loss_streak}")
    if loss_streaks:
        print(f"  Avg loss streak:  {np.mean(loss_streaks):.1f}  ({len(loss_streaks)} streaks)")

    # ── SECTION 3: Bear market focus (2020, 2022) ─────────────────────────────
    print(f"\n\n{'='*90}")
    print("BEAR MARKET FOCUS — 2020 (COVID crash) + 2022 (rate hike bear)")
    print(f"{'='*90}")
    for yr in [2020, 2022]:
        ydf  = df[df["year"] == yr]
        if ydf.empty:
            print(f"  {yr}: no data")
            continue
        pnls = ydf["pnl"].tolist()
        wr   = ydf["win"].mean() * 100
        sh   = sharpe(pnls)
        print(f"\n  {yr}:  {len(pnls)} trades | WR {wr:.1f}% | Total {M(sum(pnls))} | MaxDD {M(max_dd(pnls))} | Sharpe {sh:.2f}")
        # VIX breakdown within year
        for reg in ["Low   <15", "Mid 15-20", "High 20-30", "Spike  >30"]:
            rdf = ydf[ydf["regime"] == reg]
            if rdf.empty:
                continue
            rwr = rdf["win"].mean() * 100
            print(f"    {reg}: {len(rdf):3} trades | WR {rwr:.1f}% | Total {M(rdf['pnl'].sum())}")

    # ── SECTION 4: Final recommendation ──────────────────────────────────────
    print(f"\n\n{'='*90}")
    print("STRATEGY SUMMARY & RECOMMENDATIONS")
    print(f"{'='*90}")
    s_all  = stats(results[focus_key]["nofilter"])
    s_vix  = stats(results[focus_key]["vixfilt"])
    s_dir  = stats(results[focus_key]["dirfilt"])
    print(f"\n  Focus config: {focus_label}")
    print(f"  Period: {start_date} → {end_date}  ({n_years:.1f} years)")
    print(f"\n  {'Strategy':>22} {'Trades':>7} {'WR%':>6} {'$/yr':>9} {'Total':>9} {'MaxDD':>8} {'Sharpe':>7}")
    print("  " + "─"*72)
    for lab, s in [("All days", s_all), ("VIX<20 gate", s_vix), ("Bull days only", s_dir)]:
        if not s:
            continue
        print(f"  {lab:>22}  {s['n']:>6}  {s['wr']:>5.1f}%  {M(s['pyr'])}  {M(s['total'])}  {M(s['maxdd'])}  {s['sharpe']:>6.2f}")

    print(f"\n  Min credit: ${MIN_CREDIT:.2f} | Stop: {STOP_MULT}x EOD | Target: {TARGET_PCT:.0%} EOD | EOD prices only")
    print(f"  NOTE: No intraday stops — daily OHLCV only. Live bot uses real-time stops.")
    print(f"{'─'*90}\n")


if __name__ == "__main__":
    main()
