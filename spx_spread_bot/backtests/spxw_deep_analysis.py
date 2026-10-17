"""
SPXW 0DTE BPS Deep Analysis — Real OHLCV Prices (2023–2024)
============================================================
Extends the summary backtest with:
  - Year-by-year breakdown (2023 vs 2024)
  - Month-by-month P&L
  - VIX regime segmentation
  - Day-of-week breakdown
  - Exit reason breakdown
  - Consecutive loss streaks
  - Credit distribution
  - Direction filter sensitivity

Focus: OTM 1.0%, Width 50pt (best Sharpe from summary)
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

# ── paths ────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "databento" / "spxw_opt" / "OPRA-20260508-UGR86AMR94"
SPX_CACHE = Path("/tmp/spx_daily_cache.parquet")

# ── strategy params ───────────────────────────────────────────────────────────
MIN_CREDIT   = 0.80
STOP_MULT    = 2.0
TARGET_PCT   = 0.50
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
        print(f"SPX cache loaded: {len(df)} rows")
        return df
    print("Downloading SPX / VIX daily from yfinance...")
    spx = yf.download("^GSPC", start="2023-01-01", end="2024-12-31", auto_adjust=True, progress=False)
    vix = yf.download("^VIX",  start="2023-01-01", end="2024-12-31", auto_adjust=True, progress=False)
    # flatten MultiIndex columns if present
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
    """Return (root, expiry_date, right, strike) or None."""
    m = OCC_RE.match(sym.strip())
    if not m:
        return None
    try:
        exp = datetime.strptime(m.group("yymmdd"), "%y%m%d").date()
        strike = int(m.group("strike")) / 1000.0
        return m.group("root"), exp, m.group("right"), strike
    except Exception:
        return None


def load_spxw_daily(data_dir: Path, start: date, end: date) -> dict[date, pd.DataFrame]:
    """
    Load SPXW/SPX 0DTE put OHLCV data.
    Returns dict: trade_date -> DataFrame of puts expiring that day.
    Prices are already in dollars (no conversion needed).
    """
    print("Loading SPXW daily prices...")
    files = sorted(data_dir.glob("opra-pillar-*.ohlcv-1d.dbn.zst"))
    print(f"Files: {len(files)}")

    day_map: dict[date, list[dict]] = {}
    loaded = 0
    for f in files:
        # filename: opra-pillar-YYYYMMDD.ohlcv-1d.dbn.zst
        try:
            parts = f.stem.split(".")
            day_str = parts[0].split("-")[2]   # YYYYMMDD
            file_date = datetime.strptime(day_str, "%Y%m%d").date()
        except Exception:
            continue
        if not (start <= file_date <= end):
            continue

        try:
            store = db.DBNStore.from_file(str(f))
            df_raw = store.to_df()
        except Exception:
            continue

        rows = []
        for sym, grp in df_raw.groupby("symbol"):
            sym_str = str(sym).strip()
            parsed = parse_symbol(sym_str)
            if not parsed:
                continue
            root, exp, right, strike = parsed
            if root not in ("SPXW", "SPX"):
                continue
            if right != "P":
                continue
            if exp != file_date:      # 0DTE only
                continue
            r = grp.iloc[0]
            rows.append({
                "symbol": sym_str,
                "strike": strike,
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": int(r["volume"]),
            })

        if rows:
            day_map[file_date] = pd.DataFrame(rows)
            loaded += 1

    total_rows = sum(len(v) for v in day_map.values())
    print(f"SPXW 0DTE puts: {total_rows:,} options across {loaded} trading days\n")
    return day_map


# ── backtest engine ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    date:          date
    otm_pct:       float
    width:         int
    short_strike:  float
    long_strike:   float
    credit:        float
    exit_mark:     float
    pnl:           float
    win:           bool
    exit_reason:   str
    spx_open:      float
    spx_close:     float
    vix:           float
    direction_ok:  bool   # spx_close >= spx_open


def run_backtest(
    day_map: dict[date, pd.DataFrame],
    spx: pd.DataFrame,
    otm_pct: float,
    width: int,
    direction_filter: bool = False,
) -> list[Trade]:
    trades: list[Trade] = []

    for day, opts in sorted(day_map.items()):
        ts = pd.Timestamp(day)
        if ts not in spx.index:
            continue
        spx_row = spx.loc[ts]
        spx_open_px  = float(spx_row["spx_open"])
        spx_close_px = float(spx_row["spx_close"])
        vix_val = float(spx_row["vix_close"]) if not pd.isna(spx_row["vix_close"]) else 20.0
        direction_ok = spx_close_px >= spx_open_px

        if direction_filter and not direction_ok:
            continue

        # target short strike
        target_short = spx_open_px * (1.0 - otm_pct)

        # find closest short put
        opts = opts.copy()
        opts["dist"] = (opts["strike"] - target_short).abs()
        short_row = opts.loc[opts["dist"].idxmin()]

        short_strike = short_row["strike"]
        long_strike  = short_strike - width

        # find exact long, else nearest below short
        exact_long = opts[opts["strike"] == long_strike]
        if not exact_long.empty:
            long_row = exact_long.iloc[0]
        else:
            below = opts[opts["strike"] < short_strike]
            if below.empty:
                continue
            diff = (below["strike"] - long_strike).abs()
            long_row = below.loc[diff.idxmin()]
            long_strike = long_row["strike"]

        # entry credit (open prices)
        credit_open = float(short_row["open"]) - float(long_row["open"])
        if credit_open < MIN_CREDIT:
            continue
        if credit_open <= 0:
            continue

        # exit mark (close prices)
        mark_close = float(short_row["close"]) - float(long_row["close"])
        mark_close = max(mark_close, 0.0)

        stop_level   = credit_open * STOP_MULT
        target_level = credit_open * (1.0 - TARGET_PCT)

        if mark_close <= target_level:
            pnl = credit_open - mark_close
            exit_reason = "target"
        elif mark_close >= stop_level:
            pnl = credit_open - mark_close
            exit_reason = "stop"
        elif mark_close <= 0.05:
            pnl = credit_open
            exit_reason = "expire"
        else:
            pnl = credit_open - mark_close
            exit_reason = "close"

        # SPX/SPXW options: $100 multiplier per contract
        MULT = 100.0
        trades.append(Trade(
            date=day, otm_pct=otm_pct, width=width,
            short_strike=short_strike, long_strike=long_strike,
            credit=credit_open * MULT, exit_mark=mark_close * MULT,
            pnl=pnl * MULT, win=pnl > 0,
            exit_reason=exit_reason,
            spx_open=spx_open_px, spx_close=spx_close_px,
            vix=vix_val, direction_ok=direction_ok,
        ))

    return trades


# ── stats helpers ──────────────────────────────────────────────────────────────

def sharpe(pnls: list[float], ann: float = 252) -> float:
    a = np.array(pnls)
    if len(a) < 2 or a.std() == 0:
        return 0.0
    return a.mean() / a.std() * np.sqrt(ann)


def max_dd(pnls: list[float]) -> float:
    eq = np.cumsum(pnls)
    pk = np.maximum.accumulate(eq)
    return float((eq - pk).min())


def summary_stats(trades: list[Trade]) -> dict:
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


def vix_regime(v: float) -> str:
    if v < 15:  return "Low (<15)"
    if v < 20:  return "Mid (15-20)"
    if v < 30:  return "High (20-30)"
    return "Spike (>30)"


def fmt_money(v: float) -> str:
    return f"${v:>8.0f}"


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    START = date(2023, 1, 1)
    END   = date(2024, 12, 31)

    spx     = load_spx_daily()
    spx.index = pd.to_datetime(spx.index).normalize()

    day_map = load_spxw_daily(DATA_DIR, START, END)
    if not day_map:
        print("ERROR: no SPXW data loaded")
        return

    # ── SECTION 1: Summary sweep ────────────────────────────────────────────
    all_on:  dict[str, list[Trade]] = {}
    all_off: dict[str, list[Trade]] = {}
    for cfg in CONFIGS:
        otm, w = cfg["otm_pct"], cfg["width"]
        key = f"{otm:.1%}_{w}pt"
        all_on[key]  = run_backtest(day_map, spx, otm, w, direction_filter=True)
        all_off[key] = run_backtest(day_map, spx, otm, w, direction_filter=False)

    for (title, store) in [
        ("Direction Filter ON", all_on),
        ("Direction Filter OFF", all_off),
    ]:
        print(f"\n{'='*80}")
        print(f"SPXW 0DTE BPS — REAL OPTION PRICES | 2023–2024 | {title}")
        print(f"{'='*80}")
        print(f"  {'OTM%':>5}  {'Width':>6}  {'Trades':>7}  {'WR%':>6}  {'Avg$/tr':>8}  {'$/yr':>8}  {'Total$':>8}  {'MaxDD':>7}  {'Sharpe':>7}")
        print("─"*80)
        for cfg in CONFIGS:
            otm, w = cfg["otm_pct"], cfg["width"]
            key = f"{otm:.1%}_{w}pt"
            s = summary_stats(store[key])
            if not s:
                print(f"  {otm:.1%}  {w:>5}pt  {'—':>7}")
                continue
            print(f"  {otm:.1%}  {w:>5}pt  {s['n']:>7}  {s['wr']:>5.1f}%  ${s['avg']:>7.2f}  {fmt_money(s['pyr'])}  {fmt_money(s['total'])}  {fmt_money(s['maxdd'])}  {s['sharpe']:>6.2f}")

    print(f"\nMin credit: ${MIN_CREDIT:.2f} | Stop: {STOP_MULT}x (EOD) | Target: {TARGET_PCT:.0%} (EOD) | P&L based on open/close only")
    print("NOTE: EOD stop/target only — no intraday simulation with daily OHLCV")

    # ── SECTION 2: Deep dive ────────────────────────────────────────────────
    focus_key = f"{FOCUS_CFG['otm_pct']:.1%}_{FOCUS_CFG['width']}pt"
    otm_label = f"OTM {FOCUS_CFG['otm_pct']:.1%} | Width {FOCUS_CFG['width']}pt"

    for (label, trades) in [
        ("Direction Filter ON",  all_on[focus_key]),
        ("Direction Filter OFF", all_off[focus_key]),
    ]:
        print(f"\n\n{'='*80}")
        print(f"DEEP DIVE: {otm_label} | {label}")
        print(f"{'='*80}")
        if not trades:
            print("  No trades.")
            continue

        df = pd.DataFrame([vars(t) for t in trades])
        df["date"] = pd.to_datetime(df["date"])
        df["month"] = df["date"].dt.to_period("M").astype(str)
        df["year"]  = df["date"].dt.year
        df["dow"]   = df["date"].dt.day_name()
        df["qtr"]   = df["date"].dt.to_period("Q").astype(str)
        df["regime"]= df["vix"].apply(vix_regime)

        # 2a Year-by-year
        print(f"\n── Year-by-Year ──────────────────────────────────────────────────────────")
        print(f"  {'Year':>6}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'MaxDD':>7}  {'Sharpe':>7}")
        print("  " + "─"*60)
        for yr in sorted(df["year"].unique()):
            ydf = df[df["year"] == yr]
            pnls = ydf["pnl"].tolist()
            wr   = ydf["win"].mean() * 100
            sh   = sharpe(pnls)
            print(f"  {yr:>6}  {len(pnls):>7}  {wr:>5.1f}%  {fmt_money(sum(pnls))}  {fmt_money(max_dd(pnls))}  {sh:>6.2f}")

        # 2b Month-by-month
        print(f"\n── Month-by-Month P&L ────────────────────────────────────────────────────")
        print(f"  {'Month':>8}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'AvgCredit':>10}")
        print("  " + "─"*50)
        for month in sorted(df["month"].unique()):
            mdf = df[df["month"] == month]
            wr  = mdf["win"].mean() * 100
            tot = mdf["pnl"].sum()
            avg_credit = mdf["credit"].mean()
            print(f"  {month:>8}  {len(mdf):>7}  {wr:>5.1f}%  {fmt_money(tot)}  ${avg_credit:>8.2f}")

        # 2c Quarterly
        print(f"\n── Quarterly P&L ─────────────────────────────────────────────────────────")
        print(f"  {'Quarter':>8}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'Sharpe':>7}")
        print("  " + "─"*50)
        for q in sorted(df["qtr"].unique()):
            qdf = df[df["qtr"] == q]
            pnls = qdf["pnl"].tolist()
            wr   = qdf["win"].mean() * 100
            sh   = sharpe(pnls, ann=len(pnls) * 4)
            print(f"  {q:>8}  {len(pnls):>7}  {wr:>5.1f}%  {fmt_money(sum(pnls))}  {sh:>6.2f}")

        # 2d VIX regime
        print(f"\n── VIX Regime Breakdown ──────────────────────────────────────────────────")
        print(f"  {'Regime':>14}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'Avg$/tr':>8}  {'Sharpe':>7}")
        print("  " + "─"*65)
        for reg in ["Low (<15)", "Mid (15-20)", "High (20-30)", "Spike (>30)"]:
            rdf = df[df["regime"] == reg]
            if rdf.empty:
                continue
            pnls = rdf["pnl"].tolist()
            wr   = rdf["win"].mean() * 100
            sh   = sharpe(pnls)
            print(f"  {reg:>14}  {len(pnls):>7}  {wr:>5.1f}%  {fmt_money(sum(pnls))}  {fmt_money(np.mean(pnls))}  {sh:>6.2f}")

        # 2e Day-of-week
        print(f"\n── Day-of-Week ───────────────────────────────────────────────────────────")
        print(f"  {'Day':>10}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'Avg$/tr':>8}")
        print("  " + "─"*50)
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            ddf = df[df["dow"] == day]
            if ddf.empty:
                continue
            wr  = ddf["win"].mean() * 100
            tot = ddf["pnl"].sum()
            avg = ddf["pnl"].mean()
            print(f"  {day:>10}  {len(ddf):>7}  {wr:>5.1f}%  {fmt_money(tot)}  {fmt_money(avg)}")

        # 2f Exit reason
        print(f"\n── Exit Reason Breakdown ─────────────────────────────────────────────────")
        print(f"  {'Reason':>8}  {'Count':>7}  {'WR%':>6}  {'Total$':>8}  {'Avg$/tr':>8}")
        print("  " + "─"*50)
        for reason in ["expire", "target", "stop", "close"]:
            edf = df[df["exit_reason"] == reason]
            if edf.empty:
                continue
            wr  = edf["win"].mean() * 100
            tot = edf["pnl"].sum()
            avg = edf["pnl"].mean()
            print(f"  {reason:>8}  {len(edf):>7}  {wr:>5.1f}%  {fmt_money(tot)}  {fmt_money(avg)}")

        # 2g Consecutive loss streaks
        print(f"\n── Win/Loss Streak Analysis ──────────────────────────────────────────────")
        streak = 0
        max_win = max_loss = 0
        curr = None
        loss_streaks = []
        for win_val in df["win"].values:
            if win_val:
                if curr == "L":
                    loss_streaks.append(streak)
                curr = "W"
                streak = streak + 1 if curr == "W" else 1
                max_win = max(max_win, streak)
            else:
                if curr == "W":
                    streak = 1
                else:
                    streak = streak + 1 if curr == "L" else 1
                curr = "L"
                max_loss = max(max_loss, streak)
        if curr == "L":
            loss_streaks.append(streak)

        print(f"  Max winning streak:   {max_win} consecutive wins")
        print(f"  Max losing streak:    {max_loss} consecutive losses")
        print(f"  Avg loss streak len:  {np.mean(loss_streaks):.1f}  (total streaks: {len(loss_streaks)})")

        # 2h Credit distribution
        print(f"\n── Credit Received Distribution ──────────────────────────────────────────")
        bins   = [0.80, 1.0, 1.5, 2.0, 3.0, 5.0, 999.0]
        labels = ["$0.80-1.00", "$1.00-1.50", "$1.50-2.00", "$2.00-3.00", "$3.00-5.00", ">$5.00"]
        print(f"  {'Credit Band':>12}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'Avg$/tr':>8}")
        print("  " + "─"*55)
        for lo, hi, lab in zip(bins, bins[1:], labels):
            bdf = df[(df["credit"] >= lo) & (df["credit"] < hi)]
            if bdf.empty:
                continue
            wr  = bdf["win"].mean() * 100
            tot = bdf["pnl"].sum()
            avg = bdf["pnl"].mean()
            print(f"  {lab:>12}  {len(bdf):>7}  {wr:>5.1f}%  {fmt_money(tot)}  {fmt_money(avg)}")

    # ── SECTION 3: Direction filter sensitivity ──────────────────────────────
    print(f"\n\n{'='*80}")
    print("DIRECTION FILTER SENSITIVITY — OTM 1.0% / 50pt | 2023-2024")
    print(f"{'='*80}")

    trades_all = all_off[focus_key]
    if trades_all:
        df_all = pd.DataFrame([{
            "pnl":         t.pnl,
            "win":         t.win,
            "direction_ok": t.direction_ok,
            "spx_chg_pct": (t.spx_close - t.spx_open) / t.spx_open * 100,
        } for t in trades_all])

        bull = df_all[df_all["direction_ok"]]
        bear = df_all[~df_all["direction_ok"]]

        print(f"\n  {'Condition':>26}  {'Trades':>7}  {'WR%':>6}  {'Total$':>8}  {'Avg$/tr':>8}  {'Sharpe':>7}")
        print("  " + "─"*70)
        for label_s, sub in [
            ("SPX closed >= open (bull days)", bull),
            ("SPX closed < open (bear days)", bear),
            ("All days combined", df_all),
        ]:
            if sub.empty:
                continue
            pnls = sub["pnl"].tolist()
            wr   = sub["win"].mean() * 100
            sh   = sharpe(pnls)
            print(f"  {label_s:>26}  {len(pnls):>7}  {wr:>5.1f}%  {fmt_money(sum(pnls))}  {fmt_money(np.mean(pnls))}  {sh:>6.2f}")

        print(f"\n  SPX intraday move vs BPS performance (0-filter, all trades):")
        print(f"  {'SPX Move':>16}  {'Trades':>7}  {'WR%':>6}  {'Avg$/tr':>8}  {'Total$':>8}")
        print("  " + "─"*60)
        move_bins = [
            (-9, -1.0,  "<-1.0%"),
            (-1.0, -0.5, "-1.0% to -0.5%"),
            (-0.5, 0.0,  "-0.5% to 0%"),
            (0.0,  0.5,  "0% to +0.5%"),
            (0.5,  1.0,  "+0.5% to +1.0%"),
            (1.0,  99,   ">+1.0%"),
        ]
        for lo, hi, lab in move_bins:
            bdf = df_all[(df_all["spx_chg_pct"] > lo) & (df_all["spx_chg_pct"] <= hi)]
            if bdf.empty:
                continue
            wr  = bdf["win"].mean() * 100
            avg = bdf["pnl"].mean()
            tot = bdf["pnl"].sum()
            print(f"  {lab:>16}  {len(bdf):>7}  {wr:>5.1f}%  {fmt_money(avg)}  {fmt_money(tot)}")

    print(f"\n{'─'*80}")
    print(f"Min credit: ${MIN_CREDIT:.2f} | Stop: {STOP_MULT}x | Target: {TARGET_PCT:.0%} | EOD prices only")
    print(f"{'─'*80}\n")


if __name__ == "__main__":
    main()
