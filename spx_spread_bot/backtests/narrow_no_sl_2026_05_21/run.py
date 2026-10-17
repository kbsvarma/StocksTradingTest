"""Narrow widths × OTM × profit-target × SL grid backtest.

User's thesis: at ultra-narrow widths (1-3pt) with close-to-ATM strikes,
max loss is capped to $100-300 per spread. If profitable WITHOUT a stop-loss,
that eliminates the entire SL execution-risk class that bit us 2026-05-20 and
2026-05-21.

Grid:
  widths      = [1, 2, 3, 5]
  otm_pcts    = [0.0025, 0.005, 0.0075, 0.010]
  profit_tgts = [None, 0.50, 0.25]   # None=hold to expire, 0.50=close when mark=50% of credit
  sl_mults    = [None, 2.0, 3.0]     # None=no stop, 2.0=2x credit stop, 3.0=3x

Total: 4 × 4 × 3 × 3 = 144 configs

Constants held:
  - VIX gate: 12-25
  - Direction filter: SPX_close >= SPX_open  (proxy for "bullish session", matches prior backtests)
  - 10:30 ET entry (proxied via daily open)
  - 0DTE only
  - Commission: $2.60/spread round trip (Webull 4 legs × $0.65)

Output: results/summary.csv  +  console table sorted by net annual P&L
"""
from __future__ import annotations

import warnings
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)

# Reuse cached data from yesterday's narrow_width_sweep
CACHE_DIR = Path(__file__).parent.parent / "narrow_width_sweep_2026_05_21" / "results"
SPX_CACHE = CACHE_DIR / "spx_vix_daily.parquet"
OPTS_CACHE = CACHE_DIR / "spxw_0dte_puts.parquet"

# Strategy constants
SL_FILL_MULT = 2.5          # actual exit when SL fires (slippage past stop)
DIRECTION_FILTER = True
SLIP_PER_SPREAD_PER_SIDE = 0.05  # round-trip slippage on entry
MIN_CREDIT = 0.05
MULT = 100.0
VIX_MIN, VIX_MAX = 12.0, 25.0
COMMISSION_PER_TRADE = 2.60  # 1 spread round trip; multiplied by quantity at scale analysis

# Grid
WIDTHS = [1, 2, 3, 5]
OTM_PCTS = [0.0025, 0.005, 0.0075, 0.010]
PROFIT_TARGETS = [None, 0.50, 0.25]
SL_MULTS = [None, 2.0, 3.0]


def _log(msg: str) -> None:
    print(f"[narrow-no-sl] {msg}", flush=True)


def load_data():
    if not SPX_CACHE.exists() or not OPTS_CACHE.exists():
        _log("ERROR: cached data missing — must run narrow_width_sweep_2026_05_21 first")
        sys.exit(1)
    spx = pd.read_parquet(SPX_CACHE)
    _log(f"SPX/VIX cache: {len(spx)} rows")
    opts_df = pd.read_parquet(OPTS_CACHE)
    opts_by_day: dict[date, pd.DataFrame] = {}
    for d, g in opts_df.groupby("trading_day"):
        opts_by_day[d] = g[["strike", "open", "close"]].reset_index(drop=True)
    _log(f"options cache: {len(opts_by_day)} trading days")
    return spx, opts_by_day


@dataclass
class Trade:
    date: date
    width: float
    otm_pct: float
    profit_target: object  # None or float
    sl_mult: object        # None or float
    short_strike: float
    long_strike: float
    actual_width: float
    credit: float
    spx_open: float
    spx_low: float
    spx_close: float
    vix: float
    pnl_pts: float
    pnl_usd: float
    exit_reason: str


def simulate(opts_by_day: dict, spx_vix: pd.DataFrame,
             width: int, otm_pct: float,
             profit_target, sl_mult) -> list[Trade]:
    """Simulate a single config across the full historical range.

    Profit target logic:
      - profit_target=0.50 means: close when spread mark drops to 50% of credit
        (i.e., we've captured half the credit)
      - For 0DTE, this is a proxy for "intraday close at 50% profit"
      - Since we only have OHLCV daily data, we approximate by:
        - If close mark > 50% of credit → we COULD have hit 50% PT at some point
          (conservative: assume we did, exit at PT price = 0.5 * credit)
        - Note: this is approximate; minute-level would be more accurate

    SL logic:
      - sl_mult=2.0 means stop fires if spread mark hits 2× credit
      - Proxy: if spx_low <= short_strike (short went ITM), assume SL fires
      - sl_mult=None means no stop — hold to expiry

    Priority if both PT and SL could fire same day:
      - Assume SL fires first (worse case for our P&L, conservative)
    """
    trades = []
    for d, opts in opts_by_day.items():
        ts = pd.Timestamp(d)
        if ts not in spx_vix.index:
            continue
        row = spx_vix.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        spx_open = float(row["spx_open"])
        spx_low  = float(row["spx_low"])
        spx_close = float(row["spx_close"])
        vix = float(row["vix_close"]) if not pd.isna(row["vix_close"]) else 20.0

        # Direction filter (matches prior studies)
        if DIRECTION_FILTER and spx_close < spx_open:
            continue

        # VIX gate
        if not (VIX_MIN <= vix <= VIX_MAX):
            continue

        # Strike selection
        opts_d = opts.copy()
        target_short = spx_open * (1.0 - otm_pct)
        opts_d["d"] = (opts_d["strike"] - target_short).abs()
        if opts_d.empty:
            continue
        sr = opts_d.loc[opts_d["d"].idxmin()]
        short_strike = float(sr["strike"])
        long_target = short_strike - width
        exact = opts_d[opts_d["strike"] == long_target]
        if not exact.empty:
            lr = exact.iloc[0]
        else:
            below = opts_d[opts_d["strike"] < short_strike].copy()
            if below.empty:
                continue
            below["d"] = (below["strike"] - long_target).abs()
            lr = below.loc[below["d"].idxmin()]
        long_strike = float(lr["strike"])
        actual_width = short_strike - long_strike
        if actual_width <= 0:
            continue

        credit = float(sr["open"]) - float(lr["open"])
        if credit < MIN_CREDIT or credit <= 0:
            continue
        # Entry slippage
        credit -= 2 * SLIP_PER_SPREAD_PER_SIDE
        if credit < MIN_CREDIT:
            continue

        # Compute closing-mark intrinsic
        mark_close = max(0.0, float(sr["close"]) - float(lr["close"]))
        if mark_close > actual_width:
            mark_close = actual_width
        elif mark_close < 0:
            mark_close = 0

        # Determine exit
        sl_triggered = False
        if sl_mult is not None:
            # SL fires if spx_low <= short_strike (mark would exceed sl_mult * credit)
            # Better: only fire if intrinsic at low >= sl_mult * credit
            intrinsic_at_low = max(0.0, short_strike - spx_low) - max(0.0, long_strike - spx_low)
            intrinsic_at_low = min(intrinsic_at_low, actual_width)
            sl_threshold = credit * sl_mult
            if intrinsic_at_low >= sl_threshold:
                sl_triggered = True

        pt_triggered = False
        if profit_target is not None and not sl_triggered:
            # PT fires if mark dropped enough during day
            # Approximate: if close mark < PT level, we likely hit PT at some point
            pt_exit_mark = credit * profit_target
            if mark_close < pt_exit_mark:
                pt_triggered = True

        if sl_triggered:
            exit_mark = min(credit * SL_FILL_MULT, actual_width)
            pnl_pts = credit - exit_mark
            reason = "STOPPED"
        elif pt_triggered:
            exit_mark = credit * profit_target
            pnl_pts = credit - exit_mark
            reason = "PROFIT_TARGET"
        else:
            # Hold to expire
            if mark_close <= 0.05:
                pnl_pts = credit
                reason = "EXPIRED"
            else:
                pnl_pts = credit - mark_close
                reason = "EXPIRED_ITM"
                if -pnl_pts > actual_width:
                    pnl_pts = -actual_width

        pnl_usd = pnl_pts * MULT - COMMISSION_PER_TRADE
        trades.append(Trade(
            date=d, width=width, otm_pct=otm_pct,
            profit_target=profit_target, sl_mult=sl_mult,
            short_strike=short_strike, long_strike=long_strike,
            actual_width=actual_width, credit=credit,
            spx_open=spx_open, spx_low=spx_low, spx_close=spx_close, vix=vix,
            pnl_pts=pnl_pts, pnl_usd=pnl_usd, exit_reason=reason,
        ))
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = (df["pnl_usd"] > 0).sum()
    losses = (df["pnl_usd"] <= 0).sum()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    annual = df.groupby("year")["pnl_usd"].sum()
    cum = df.sort_values("date")["pnl_usd"].cumsum()
    drawdown = cum - cum.cummax()
    return {
        "trades": len(df),
        "win_rate": float(wins / len(df)),
        "stops": int((df["exit_reason"] == "STOPPED").sum()),
        "profit_targets": int((df["exit_reason"] == "PROFIT_TARGET").sum()),
        "expired": int((df["exit_reason"] == "EXPIRED").sum()),
        "expired_itm": int((df["exit_reason"] == "EXPIRED_ITM").sum()),
        "total_pnl": float(df["pnl_usd"].sum()),
        "avg_pnl_per_trade": float(df["pnl_usd"].mean()),
        "best_trade": float(df["pnl_usd"].max()),
        "worst_trade": float(df["pnl_usd"].min()),
        "max_drawdown": float(drawdown.min()),
        "annual_avg_1x": float(annual.mean()),
        "annual_min": float(annual.min()),
        "annual_max": float(annual.max()),
        "years_covered": int(df["year"].nunique()),
        "avg_credit": float(df["credit"].mean()),
        # Commission-adjusted at scale (commission scales linearly with contracts)
        # annual_per_contract is already net of 1x commission
        "annual_per_contract_net": float(annual.mean()),
        "annual_at_5x": float(annual.mean() * 5),  # commissions already in per-trade pnl
        "annual_at_10x": float(annual.mean() * 10),
    }


def main():
    spx, opts = load_data()
    rows = []
    total = len(WIDTHS) * len(OTM_PCTS) * len(PROFIT_TARGETS) * len(SL_MULTS)
    i = 0
    for w in WIDTHS:
        for otm in OTM_PCTS:
            for pt in PROFIT_TARGETS:
                for sl in SL_MULTS:
                    i += 1
                    pt_label = "none" if pt is None else f"{int(pt*100)}%"
                    sl_label = "none" if sl is None else f"{sl}x"
                    _log(f"[{i}/{total}] width={w} otm={otm:.4f} PT={pt_label} SL={sl_label}")
                    trades = simulate(opts, spx, w, otm, pt, sl)
                    s = summarize(trades)
                    s.update({
                        "width": w, "otm_pct": otm,
                        "profit_target": pt_label, "sl_mult": sl_label,
                    })
                    rows.append(s)
                    if s.get("trades", 0) > 0:
                        _log(f"  → {s['trades']} trades, win={s['win_rate']:.1%}, "
                             f"annual_1x=${s['annual_avg_1x']:,.0f}, worst=${s['worst_trade']:,.0f}, "
                             f"DD=${s['max_drawdown']:,.0f}")

    out_df = pd.DataFrame(rows)
    for c in ("annual_avg_1x", "total_pnl", "win_rate", "worst_trade", "max_drawdown"):
        if c not in out_df.columns:
            out_df[c] = 0
        out_df[c] = out_df[c].fillna(0)
    out_df = out_df.sort_values("annual_avg_1x", ascending=False)
    out_df.to_csv(OUT / "summary.csv", index=False)

    _log("")
    _log("=" * 100)
    _log("TOP 20 BY ANNUAL P&L (1× CONTRACT, NET OF $2.60/spread COMMISSION)")
    _log("=" * 100)
    cols = ["width", "otm_pct", "profit_target", "sl_mult", "trades", "win_rate",
            "stops", "profit_targets", "annual_avg_1x", "worst_trade", "max_drawdown"]
    print(out_df[cols].head(20).to_string(index=False))
    _log("")
    _log("TOP 5 NO-SL (the user's thesis):")
    no_sl = out_df[out_df["sl_mult"] == "none"]
    print(no_sl[cols].head(5).to_string(index=False))
    _log("")
    _log(f"done. {OUT / 'summary.csv'}")


if __name__ == "__main__":
    main()
