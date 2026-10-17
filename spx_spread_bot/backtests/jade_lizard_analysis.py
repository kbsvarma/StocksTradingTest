"""
Jade Lizard Analysis — Built on Real BPS Trade Data
=====================================================
Structure:
  Leg 1+2 : Bull Put Spread  — taken from actual BPS trade results (real fills)
  Leg 3+4 : Bear Call Spread — priced via Black-Scholes using actual SPX + VIX

No-upside-risk condition (Jade Lizard rule):
  total_credit (put + call, in option $) >= call_spread_width * 100
  i.e. total credit per contract >= max loss on call side

Exit logic:
  Put spread  : same as original BPS exit (stop, profit target, expiry)
  Call spread : checked against SPX close — if SPX > short_call at expiry,
                call spread loses proportionally; if stop triggered on put side,
                market moved down → call spread almost always expires worthless

Sweeps:
  - Call spread OTM% (0.5%, 1.0%, 1.5%, 2.0%)
  - Call spread width (5pt, 10pt, 25pt)
  - VIX regime filter
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

TRADES_CSV = Path(__file__).parent / "ten_year" / "trade_results.csv"
DAILY_CSV  = Path(__file__).parent / "ten_year" / "spx_daily_ohlc_bb.csv"

ENTRY_TIME_HOURS = 6.25 / (6.5 * 252)   # 9:45am entry, ~6.25hrs to close
RISK_FREE        = 0.05                  # approx 5% for recent period, close enough
CONTRACTS        = 12                    # matches backtest default


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def call_spread_credit(S: float, vix: float, short_call: float,
                        long_call: float, T: float = ENTRY_TIME_HOURS) -> float:
    """Mid-market credit for a bear call spread (short lower, long higher)."""
    sigma = vix / 100.0
    short_val = bs_call(S, short_call, T, RISK_FREE, sigma)
    long_val  = bs_call(S, long_call,  T, RISK_FREE, sigma)
    return round(max(short_val - long_val, 0.0), 2)


# ── load data ─────────────────────────────────────────────────────────────────

def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = pd.read_csv(TRADES_CSV, parse_dates=["Date"])
    trades["date"] = trades["Date"].dt.normalize()

    daily = pd.read_csv(DAILY_CSV, index_col=0, parse_dates=True)
    daily.index = pd.to_datetime(daily.index).normalize()
    daily.index.name = "date"

    # attach close price to each trade
    trades = trades.merge(
        daily[["close"]].rename(columns={"close": "spx_close"}),
        left_on="date", right_index=True, how="left"
    )
    return trades, daily


# ── core simulation ───────────────────────────────────────────────────────────

def simulate(trades: pd.DataFrame,
             call_otm_pct: float = 0.010,
             call_width:   float = 10.0) -> pd.DataFrame:
    results = []

    for _, t in trades.iterrows():
        S   = t["SPX price at entry"]
        vix = t["VIX at entry"]
        spx_close = t.get("spx_close", np.nan)

        # ── put spread (real data) ────────────────────────────────────────────
        put_credit     = t["Credit received"]          # $ per share
        put_contracts  = int(t.get("Contracts", CONTRACTS))
        put_pnl        = t["Total PnL"]                # actual realised

        # ── call spread (BSM estimate) ────────────────────────────────────────
        short_call = round(S * (1 + call_otm_pct) / 5) * 5   # round to 5pt strike
        long_call  = short_call + call_width

        call_credit = call_spread_credit(S, vix, short_call, long_call)

        # call spread P&L at expiry / exit
        exit_reason = t["Exit reason"]
        call_pnl_per = 0.0

        if exit_reason == "stop_loss":
            # market dropped hard → call OTM, expires worthless → full call credit
            call_pnl_per = call_credit * 100.0

        elif pd.notna(spx_close):
            if spx_close <= short_call:
                # closed below short call → call spread expires worthless
                call_pnl_per = call_credit * 100.0
            elif spx_close >= long_call:
                # closed above long call → full loss on call spread
                call_pnl_per = (call_credit - call_width) * 100.0
            else:
                # partial loss
                intrinsic = spx_close - short_call
                call_pnl_per = (call_credit - intrinsic) * 100.0
        else:
            call_pnl_per = call_credit * 100.0   # assume expires worthless if no data

        call_pnl_total = call_pnl_per * put_contracts

        # ── combined ─────────────────────────────────────────────────────────
        total_credit = put_credit + call_credit
        no_upside_risk = total_credit * 100.0 >= call_width * 100.0

        combined_pnl = put_pnl + call_pnl_total

        results.append(dict(
            date          = t["date"],
            spx_entry     = S,
            vix           = vix,
            put_credit    = put_credit,
            call_credit   = round(call_credit, 2),
            total_credit  = round(total_credit, 2),
            short_call    = short_call,
            long_call     = long_call,
            call_width    = call_width,
            no_upside_risk = no_upside_risk,
            exit_reason   = exit_reason,
            put_pnl       = put_pnl,
            call_pnl      = round(call_pnl_total, 2),
            combined_pnl  = round(combined_pnl, 2),
            bps_pnl       = put_pnl,
        ))

    return pd.DataFrame(results)


# ── metrics ───────────────────────────────────────────────────────────────────

def metrics(df: pd.DataFrame, label: str, col: str = "combined_pnl") -> dict:
    p   = df[col].values
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1.0
    return dict(label=label, n=len(df),
                total=round(float(p.sum()), 0),
                win=round(float((p > 0).mean()), 3),
                avg=round(avg, 0),
                dd=round(dd, 0),
                sharpe=round((avg / std) * np.sqrt(252) if std > 0 else 0, 3))


def ptable(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'═'*88}")
        print(f"  {title}")
        print(f"{'═'*88}")
    hdr = f"{'':44} {'N':>4} {'Total$':>10} {'Win%':>6} {'Avg$':>7} {'MaxDD':>10} {'Sharpe':>7}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        marker = " ★" if r.get("sharpe", 0) > 4.594 else ""
        print(f"{r['label']:<44} {r['n']:>4} ${r['total']:>9,.0f} "
              f"{r['win']*100:>5.1f}% ${r['avg']:>6,.0f} ${r['dd']:>9,.0f} "
              f"{r['sharpe']:>7.3f}{marker}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    trades, _ = load()
    print(f"Loaded {len(trades)} BPS trades  "
          f"({trades['date'].min().date()} → {trades['date'].max().date()})\n")

    bps_ref = metrics(
        trades.assign(combined_pnl=trades["Total PnL"]),
        "── BPS baseline (actual) ──"
    )

    # ── 1. call spread config sweep ───────────────────────────────────────────
    rows = [bps_ref]
    for otm in [0.005, 0.010, 0.015, 0.020]:
        for width in [5, 10, 25]:
            df = simulate(trades, call_otm_pct=otm, call_width=width)
            label = f"JL call {otm*100:.1f}%OTM  width={int(width)}pt"
            rows.append(metrics(df, label))
    ptable(rows, "1. Jade Lizard — call spread config sweep")

    # ── 2. breakdown: what does the call spread actually add ──────────────────
    best = simulate(trades, call_otm_pct=0.010, call_width=10)
    print(f"\n{'═'*60}")
    print("  2. Call spread contribution breakdown  (1% OTM, 10pt wide)")
    print(f"{'═'*60}")
    print(f"  Avg call credit collected:    ${best['call_credit'].mean():.2f}  (${best['call_credit'].mean()*100:.0f}/contract)")
    print(f"  Avg put credit:               ${best['put_credit'].mean():.2f}  (${best['put_credit'].mean()*100:.0f}/contract)")
    print(f"  Avg total credit:             ${best['total_credit'].mean():.2f}  (${best['total_credit'].mean()*100:.0f}/contract)")
    print(f"  Call spread expires worthless:{( best['call_pnl'] > 0).mean()*100:.0f}% of trades")
    print(f"  Avg call PnL per trade:       ${best['call_pnl'].mean():.0f}")
    print(f"  No-upside-risk condition met: {best['no_upside_risk'].mean()*100:.0f}% of trades")
    print()
    no_risk = best[best["no_upside_risk"]]
    has_risk = best[~best["no_upside_risk"]]
    print(f"  When NO upside risk ({len(no_risk)} trades): avg PnL ${no_risk['combined_pnl'].mean():.0f}  win {(no_risk['combined_pnl']>0).mean()*100:.0f}%")
    print(f"  When upside risk    ({len(has_risk)} trades): avg PnL ${has_risk['combined_pnl'].mean():.0f}  win {(has_risk['combined_pnl']>0).mean()*100:.0f}%")

    # ── 3. call credit by VIX regime ─────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  3. Call spread credit by VIX level  (1% OTM, 10pt wide)")
    print(f"{'═'*60}")
    best["vix_bucket"] = pd.cut(best["vix"], bins=[0,15,20,25,30,100],
                                 labels=["<15","15-20","20-25","25-30",">30"])
    grp = best.groupby("vix_bucket", observed=True).agg(
        trades=("call_credit","count"),
        avg_call_credit=("call_credit","mean"),
        avg_total_credit=("total_credit","mean"),
        no_upside_pct=("no_upside_risk","mean"),
        avg_combined_pnl=("combined_pnl","mean"),
    ).round(2)
    grp["no_upside_pct"] = (grp["no_upside_pct"] * 100).round(0).astype(int).astype(str) + "%"
    print(grp.to_string())

    # ── 4. year by year ───────────────────────────────────────────────────────
    rows = [bps_ref]
    best["year"] = pd.to_datetime(best["date"]).dt.year
    for yr in sorted(best["year"].unique()):
        sub = best[best["year"] == yr]
        rows.append(metrics(sub, str(yr)))
    ptable(rows, "4. Year-by-year  (JL: 1% OTM call, 10pt wide)")

    # ── 5. stop loss trades — does the call side help ─────────────────────────
    stops = best[best["exit_reason"] == "stop_loss"]
    wins_eod = best[best["exit_reason"].isin(["expiry","profit_target"])]
    print(f"\n{'═'*60}")
    print("  5. Stop loss events — call spread impact")
    print(f"{'═'*60}")
    print(f"  Stop loss trades: {len(stops)}")
    print(f"  Avg put PnL on stop:      ${stops['put_pnl'].mean():,.0f}")
    print(f"  Avg call PnL on stop:     ${stops['call_pnl'].mean():,.0f}  (call expired worthless = credit kept)")
    print(f"  Avg combined on stop:     ${stops['combined_pnl'].mean():,.0f}")
    print(f"  Net improvement per stop: ${(stops['combined_pnl'] - stops['bps_pnl']).mean():,.0f}")
    print()
    print(f"  Normal exits (expiry+target): {len(wins_eod)}")
    print(f"  Avg put PnL:      ${wins_eod['put_pnl'].mean():,.0f}")
    print(f"  Avg call PnL:     ${wins_eod['call_pnl'].mean():,.0f}")
    print(f"  Avg combined PnL: ${wins_eod['combined_pnl'].mean():,.0f}")

    # ── 6. honest caveats ────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  6. Caveats on this analysis")
    print(f"{'═'*60}")
    print("  - Call prices are BSM estimates, not real fills")
    print("  - BSM assumes constant vol — 0DTE options have vol smile/skew")
    print("  - Real call credits will be LOWER than BSM (OTM calls are cheap 0DTE)")
    print("  - Stop logic for call spread on intraday spikes is simplified")
    print("  - 3-leg fill on SPX 0DTE at open adds slippage vs 2-leg BPS")
    print("  - BSM typically OVERestimates 0DTE OTM call value → real edge is smaller")


if __name__ == "__main__":
    main()
