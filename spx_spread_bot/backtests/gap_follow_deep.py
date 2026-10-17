"""
Gap Follow — Deep Dive
======================
SPX opens >X% away from prior close → trade in that direction, exit at close.

Explores:
  - Gap size threshold sweep
  - Up gaps vs down gaps separately
  - Day-of-week breakdown
  - VIX regime breakdown
  - Gap size buckets
  - Year-by-year performance
  - Exit variants (EOD / profit target / stop loss)
  - Combo: gap follow + VIX filter
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_CSV = Path(__file__).parent / "ten_year" / "spx_vix_daily.csv"
PPT = 50  # dollars per point


# ── load ──────────────────────────────────────────────────────────────────────

def load() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, index_col="date", parse_dates=True)
    df["prev_close"] = df["spx_close"].shift(1)
    df["gap_pct"]    = (df["spx_open"] - df["prev_close"]) / df["prev_close"]
    df["day_ret"]    = (df["spx_close"] - df["spx_open"])          # open→close points
    df["gap_pts"]    = df["spx_open"] - df["prev_close"]
    df["dow"]        = df.index.dayofweek                          # 0=Mon
    df["month"]      = df.index.month
    df["year"]       = df.index.year
    df["gap_dir"]    = np.where(df["gap_pct"] > 0, "up", "down")
    return df.dropna(subset=["prev_close"])


# ── core trade builder ────────────────────────────────────────────────────────

def trades_for(df: pd.DataFrame,
               min_gap: float = 0.005,
               direction: str = "both",       # 'up' | 'down' | 'both'
               vix_min: float = 0.0,
               vix_max: float = 999.0,
               profit_target_pts: float | None = None,
               stop_loss_pts: float | None = None) -> pd.DataFrame:
    mask = df["gap_pct"].abs() >= min_gap
    if direction == "up":
        mask &= df["gap_pct"] > 0
    elif direction == "down":
        mask &= df["gap_pct"] < 0
    mask &= df["vix_close"].between(vix_min, vix_max)

    out = df[mask].copy()
    out["direction"] = np.where(out["gap_pct"] > 0, "long", "short")
    out["mult"]      = np.where(out["direction"] == "long", 1, -1)

    if profit_target_pts or stop_loss_pts:
        pnls, exits = [], []
        for _, r in out.iterrows():
            raw = r["day_ret"] * r["mult"]
            if profit_target_pts and raw >= profit_target_pts:
                pnl = profit_target_pts * PPT
                ex  = "target"
            elif stop_loss_pts and raw <= -stop_loss_pts:
                pnl = -stop_loss_pts * PPT
                ex  = "stop"
            else:
                pnl = raw * PPT
                ex  = "eod"
            pnls.append(pnl)
            exits.append(ex)
        out["pnl"]       = pnls
        out["exit_type"] = exits
    else:
        out["pnl"]       = out["day_ret"] * out["mult"] * PPT
        out["exit_type"] = "eod"

    return out


def metrics(t: pd.DataFrame, label: str = "") -> dict:
    if t.empty:
        return dict(label=label, n=0, total=0, win=0, avg=0, dd=0, sharpe=0)
    p   = t["pnl"].values
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1.0
    return dict(label=label, n=len(t),
                total=round(float(p.sum()), 0),
                win=round(float((p > 0).mean()), 3),
                avg=round(avg, 0),
                dd=round(dd, 0),
                sharpe=round((avg / std) * np.sqrt(252) if std > 0 else 0, 3))


def ptable(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'─'*82}")
        print(f"  {title}")
        print(f"{'─'*82}")
    hdr = f"{'':42} {'N':>5} {'Total$':>10} {'Win%':>6} {'Avg$':>7} {'MaxDD':>10} {'Sharpe':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['label']:<42} {r['n']:>5} ${r['total']:>9,.0f} "
              f"{r['win']*100:>5.1f}% ${r['avg']:>6,.0f} ${r['dd']:>9,.0f} {r['sharpe']:>7.3f}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load()
    print(f"SPX data: {df.index[0].date()} → {df.index[-1].date()}  ({len(df)} bars)")

    base = trades_for(df, min_gap=0.005)
    print(f"Base universe (gap >0.5%): {len(base)} days\n")

    # ── 1. threshold sweep ────────────────────────────────────────────────────
    rows = []
    for thr in [0.002, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.025]:
        t = trades_for(df, min_gap=thr)
        rows.append(metrics(t, f"gap > {thr*100:.1f}%"))
    ptable(rows, "1. Gap threshold sweep")

    # ── 2. up vs down gaps ────────────────────────────────────────────────────
    rows = []
    for d, label in [("up", "Up gaps only  (long)"), ("down", "Down gaps only (short)"), ("both", "Both directions")]:
        t = trades_for(df, min_gap=0.005, direction=d)
        rows.append(metrics(t, label))
    ptable(rows, "2. Up gaps vs down gaps (threshold 0.5%)")

    # ── 3. day of week ────────────────────────────────────────────────────────
    dow_names = {0:"Monday", 1:"Tuesday", 2:"Wednesday", 3:"Thursday", 4:"Friday"}
    rows = []
    for d in range(5):
        sub = base[base["dow"] == d]
        rows.append(metrics(sub, dow_names[d]))
    ptable(rows, "3. Day-of-week breakdown (gap >0.5%)")

    # ── 4. VIX regime ─────────────────────────────────────────────────────────
    rows = []
    for lo, hi, label in [(0,15,"VIX < 15  (calm)"), (15,20,"VIX 15–20"),
                           (20,25,"VIX 20–25"), (25,999,"VIX > 25  (fear)")]:
        t = trades_for(df, min_gap=0.005, vix_min=lo, vix_max=hi)
        rows.append(metrics(t, label))
    ptable(rows, "4. VIX regime breakdown (gap >0.5%)")

    # ── 5. gap size buckets ───────────────────────────────────────────────────
    rows = []
    buckets = [(0.005,0.010),(0.010,0.015),(0.015,0.020),(0.020,0.030),(0.030,1.0)]
    for lo, hi in buckets:
        sub = base[base["gap_pct"].abs().between(lo, hi)]
        rows.append(metrics(sub, f"gap {lo*100:.1f}–{hi*100:.1f}%"))
    ptable(rows, "5. Gap size buckets")

    # ── 6. year-by-year ───────────────────────────────────────────────────────
    rows = []
    for yr in sorted(base["year"].unique()):
        sub = base[base["year"] == yr]
        rows.append(metrics(sub, str(yr)))
    ptable(rows, "6. Year-by-year performance (gap >0.5%)")

    # ── 7. exit variant sweep ─────────────────────────────────────────────────
    rows = []
    rows.append(metrics(trades_for(df, 0.005), "EOD exit (baseline)"))
    for pt in [5, 10, 15, 20]:
        t = trades_for(df, 0.005, profit_target_pts=pt)
        rows.append(metrics(t, f"Profit target {pt}pts, EOD fallback"))
    for sl in [5, 10, 15, 20]:
        t = trades_for(df, 0.005, stop_loss_pts=sl)
        rows.append(metrics(t, f"Stop loss {sl}pts, EOD fallback"))
    for pt, sl in [(10,10),(15,10),(20,15),(15,20)]:
        t = trades_for(df, 0.005, profit_target_pts=pt, stop_loss_pts=sl)
        rows.append(metrics(t, f"PT={pt}pts / SL={sl}pts"))
    ptable(rows, "7. Exit variant sweep (gap >0.5%, both directions)")

    # ── 8. best combo filter ─────────────────────────────────────────────────
    rows = []
    rows.append(metrics(trades_for(df, 0.005), "No filter (baseline)"))
    rows.append(metrics(trades_for(df, 0.005, vix_min=15),         "VIX > 15"))
    rows.append(metrics(trades_for(df, 0.005, vix_max=25),         "VIX < 25"))
    rows.append(metrics(trades_for(df, 0.010),                     "gap > 1.0%"))
    rows.append(metrics(trades_for(df, 0.010, vix_max=25),         "gap>1.0% + VIX<25"))
    rows.append(metrics(trades_for(df, 0.010, vix_min=15, vix_max=25), "gap>1.0% + VIX 15-25"))

    # Monday gap follow specifically
    sub = base[base["dow"] == 0]
    rows.append(metrics(sub, "Monday gaps only"))
    sub2 = trades_for(df, 0.010)[trades_for(df, 0.010)["dow"] == 0]
    rows.append(metrics(sub2, "Monday + gap>1.0%"))

    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    ptable(rows, "8. Best combo filters")

    # ── 9. translate to options ───────────────────────────────────────────────
    print(f"\n{'─'*82}")
    print("  9. Options translation — what this means for SPX spreads")
    print(f"{'─'*82}")
    best = trades_for(df, 0.010)
    up_gaps   = best[best["direction"] == "long"]
    down_gaps = best[best["direction"] == "short"]
    print(f"\n  Up gaps (>{0.010*100:.0f}%):   {len(up_gaps)} days — SPX opens above prior close")
    print(f"    → Bull Call Spread or skip BPS (market already stretched up)")
    print(f"    → Avg intraday gain: {up_gaps['day_ret'].mean():.1f}pts  |  Win rate: {(up_gaps['pnl']>0).mean()*100:.0f}%")
    print(f"\n  Down gaps (>{0.010*100:.0f}%): {len(down_gaps)} days — SPX opens below prior close")
    print(f"    → Bull Put Spread (fade the fear, sell downside premium)")
    print(f"    → Avg intraday recovery: {down_gaps['day_ret'].mean():.1f}pts  |  Win rate: {(down_gaps['pnl']>0).mean()*100:.0f}%")
    print(f"\n  Key insight: down gap days that CONTINUE lower are your BPS stop-loss events.")

    # how many BPS stop losses coincided with down gap days
    bps = pd.read_csv(Path(__file__).parent / "ten_year" / "trade_results.csv", parse_dates=["Date"])
    bps_stops = bps[bps["Exit reason"] == "stop_loss"][["Date","SPX price at entry","Total PnL"]]
    bps_stops["date"] = bps_stops["Date"].dt.normalize()
    down_gap_dates = set(down_gaps.index.normalize())
    bps_stops["on_down_gap"] = bps_stops["date"].isin(down_gap_dates)
    n_stops = len(bps_stops)
    n_on_gap = bps_stops["on_down_gap"].sum()
    pnl_on_gap = bps_stops[bps_stops["on_down_gap"]]["Total PnL"].sum()
    print(f"\n  BPS stop-loss events: {n_stops} total")
    print(f"  Of those, {n_on_gap} ({n_on_gap/n_stops*100:.0f}%) happened on down-gap days")
    print(f"  PnL from BPS on down-gap days: ${pnl_on_gap:,.0f}")
    print(f"\n  → Skipping BPS on down gaps >1% would have avoided ${abs(pnl_on_gap):,.0f} in losses")


if __name__ == "__main__":
    main()
