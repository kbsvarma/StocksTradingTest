"""
Gap Follow Backtest — MES Futures (Real Intraday Data, Vectorized)
==================================================================
Uses actual 1-minute MES bars (2019–2026).

Entry  : first 1-min bar at/after 09:30 on days SPX-equivalent gap > threshold
Stop   : X pts against fill — detected bar-by-bar via cummin/cummax per session
Exit   : stop hit OR last bar at/before 15:55

MES = $5 / point
Slippage: entry 1.5pt, exit 0.5pt  (conservative gap-open assumption)
Commission: $1.24 round trip
"""

from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

DATA_CSV = Path(__file__).parent / "data" / "mes_1min.csv"
PPT      = 5
E_SLIP   = 1.5
X_SLIP   = 0.5
COMM     = 1.24


def load() -> pd.DataFrame:
    print("Loading MES 1-min data...", flush=True)
    df = pd.read_csv(DATA_CSV)
    df["ts"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert("America/New_York")
    df = df.sort_values("ts").reset_index(drop=True)
    t = df["ts"].dt.time
    rth_start = pd.Timestamp("09:30").time()
    rth_end   = pd.Timestamp("16:00").time()
    df = df[(t >= rth_start) & (t <= rth_end)].copy()
    df["date"]    = df["ts"].dt.normalize().dt.tz_localize(None)
    df["bar_time"] = df["ts"].dt.time
    print(f"RTH bars: {len(df):,}  |  days: {df['date'].nunique()}", flush=True)
    return df


def build_daily(df: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day: prev_close, open_bar open price."""
    # last bar of each day (RTH close)
    daily_close = df.groupby("date")["close"].last().rename("rth_close")

    # first bar at/after 09:30
    rth_open_bars = df[df["bar_time"] >= pd.Timestamp("09:30").time()]
    daily_open = rth_open_bars.groupby("date")[["open", "ts"]].first()
    daily_open.columns = ["open_price", "open_ts"]

    out = pd.concat([daily_close, daily_open], axis=1).dropna()
    out["prev_close"] = out["rth_close"].shift(1)
    out = out.dropna(subset=["prev_close"])
    out["gap_pct"] = (out["open_price"] - out["prev_close"]) / out["prev_close"]
    return out


def simulate(df: pd.DataFrame, daily: pd.DataFrame,
             gap_min: float, stop_pts: float,
             e_slip: float = E_SLIP, x_slip: float = X_SLIP,
             comm: float = COMM) -> pd.DataFrame:

    trade_days = daily[daily["gap_pct"].abs() >= gap_min].copy()
    trade_days["direction"] = np.where(trade_days["gap_pct"] > 0, "long", "short")
    trade_days["mult"]      = np.where(trade_days["direction"] == "long", 1, -1)
    trade_days["fill"]      = trade_days["open_price"] + trade_days["mult"] * e_slip
    trade_days["stop_lvl"]  = trade_days["fill"] - trade_days["mult"] * stop_pts

    eod_time = pd.Timestamp("15:55").time()

    results = []
    for date, day in trade_days.iterrows():
        session = df[(df["date"] == date) &
                     (df["ts"] >= day["open_ts"]) &
                     (df["bar_time"] <= eod_time)]
        if session.empty:
            continue

        fill  = day["fill"]
        stop  = day["stop_lvl"]
        mult  = day["mult"]

        if mult == 1:   # long — stop hit when low <= stop
            stop_mask = session["low"] <= stop
        else:           # short — stop hit when high >= stop
            stop_mask = session["high"] >= stop

        if stop_mask.any():
            exit_price  = stop - mult * x_slip   # stopped out
            exit_reason = "stop"
        else:
            exit_price  = session.iloc[-1]["close"] - mult * x_slip
            exit_reason = "eod"

        pnl = mult * (exit_price - fill) * PPT - comm
        results.append(dict(
            date        = date,
            year        = pd.Timestamp(date).year,
            dow         = pd.Timestamp(date).dayofweek,
            direction   = day["direction"],
            gap_pct     = round(day["gap_pct"] * 100, 3),
            fill        = round(fill, 2),
            stop_lvl    = round(stop, 2),
            exit_price  = round(exit_price, 2),
            exit_reason = exit_reason,
            pts         = round(mult * (exit_price - fill), 2),
            pnl         = round(pnl, 2),
        ))

    return pd.DataFrame(results)


def metrics(t: pd.DataFrame, label: str = "") -> dict:
    if t is None or t.empty:
        return dict(label=label, n=0, total=0, win=0, avg=0, dd=0, sharpe=0, stop_pct=0)
    p   = t["pnl"].values
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1.0
    sh  = (avg / std) * np.sqrt(252) if std > 0 else 0.0
    sp  = (t["exit_reason"] == "stop").mean() if "exit_reason" in t.columns else 0
    return dict(label=label, n=len(t), total=round(float(p.sum()), 0),
                win=round(float((p > 0).mean()), 3), avg=round(avg, 2),
                dd=round(dd, 0), sharpe=round(sh, 3), stop_pct=round(sp, 3))


def ptable(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'═'*94}")
        print(f"  {title}")
        print(f"{'═'*94}")
    hdr = f"{'':42} {'N':>5} {'Total$':>9} {'Win%':>6} {'Avg$':>7} {'MaxDD':>9} {'Sharpe':>7} {'Stop%':>6}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['label']:<42} {r['n']:>5} ${r['total']:>8,.0f} "
              f"{r['win']*100:>5.1f}% ${r['avg']:>6,.1f} ${r['dd']:>8,.0f} "
              f"{r['sharpe']:>7.3f} {r['stop_pct']*100:>5.0f}%")


def main() -> None:
    df     = load()
    daily  = build_daily(df)
    print(f"Trade-eligible days: {len(daily)}\n", flush=True)

    base = simulate(df, daily, gap_min=0.005, stop_pts=5.0)
    print(f"Base trades (gap>0.5%, stop=5pt, slip=1.5/0.5pt, comm=$1.24): {len(base)}\n", flush=True)

    # 1. stop sweep
    rows = []
    for sl in [3, 5, 8, 10, 15, 20, 9999]:
        t = simulate(df, daily, 0.005, sl)
        rows.append(metrics(t, f"stop={sl}pt" if sl < 9999 else "no stop (EOD only)"))
    ptable(rows, "1. Stop loss sweep  (gap>0.5%, slip=1.5/0.5pt)")

    # 2. gap threshold sweep
    rows = []
    for g in [0.003, 0.005, 0.007, 0.010, 0.015, 0.020]:
        t = simulate(df, daily, g, 5.0)
        rows.append(metrics(t, f"gap>{g*100:.1f}%"))
    ptable(rows, "2. Gap threshold sweep  (stop=5pt)")

    # 3. up vs down
    up = base[base["direction"] == "long"]
    dn = base[base["direction"] == "short"]
    ptable([metrics(up,"Up gaps (long)"), metrics(dn,"Down gaps (short)"), metrics(base,"Both")],
           "3. Up vs down gaps")

    # 4. day of week
    dow_names = {0:"Monday",1:"Tuesday",2:"Wednesday",3:"Thursday",4:"Friday"}
    ptable([metrics(base[base["dow"]==d], dow_names[d]) for d in range(5)],
           "4. Day of week")

    # 5. year by year
    ptable([metrics(base[base["year"]==yr], str(yr)) for yr in sorted(base["year"].unique())],
           "5. Year by year")

    # 6. slippage sensitivity
    rows = []
    for es, xs in [(0.0,0.0),(0.5,0.25),(1.0,0.5),(1.5,0.5),(2.0,1.0),(3.0,1.5)]:
        t = simulate(df, daily, 0.005, 5.0, e_slip=es, x_slip=xs)
        rows.append(metrics(t, f"entry_slip={es}  exit_slip={xs}"))
    ptable(rows, "6. Slippage sensitivity  (gap>0.5%, stop=5pt)")

    # 7. best combos
    rows = []
    rows.append(metrics(base, "Baseline (gap>0.5%, stop=5)"))
    rows.append(metrics(base[base["dow"] != 3], "Skip Thursday"))
    for g, sl in [(0.005,8),(0.007,5),(0.010,5),(0.010,8),(0.005,5)]:
        t = simulate(df, daily, g, sl)
        rows.append(metrics(t, f"gap>{g*100:.1f}%  stop={sl}pt"))
    t_no_thu = simulate(df, daily, 0.005, 5.0)
    t_no_thu = t_no_thu[t_no_thu["dow"] != 3]
    rows.append(metrics(t_no_thu, "gap>0.5% skip Thu"))
    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    ptable(rows, "7. Best combos")

    # 8. detailed stats
    wins  = base[base["pnl"] > 0]
    loses = base[base["pnl"] <= 0]
    stops = base[base["exit_reason"] == "stop"]
    eods  = base[base["exit_reason"] == "eod"]
    print(f"\n{'═'*60}")
    print("  8. Detailed trade stats (baseline)")
    print(f"{'═'*60}")
    print(f"  Trades:              {len(base)}")
    print(f"  Stop-outs:           {len(stops)} ({len(stops)/len(base)*100:.0f}%)")
    print(f"  EOD exits:           {len(eods)} ({len(eods)/len(base)*100:.0f}%)")
    print(f"  Winners:             {len(wins)} ({len(wins)/len(base)*100:.0f}%)")
    print(f"  Losers:              {len(loses)} ({len(loses)/len(base)*100:.0f}%)")
    print(f"  Avg winner:          ${wins['pnl'].mean():,.1f}")
    print(f"  Avg loser:           ${loses['pnl'].mean():,.1f}")
    print(f"  Largest win:         ${wins['pnl'].max():,.1f}  ({wins.loc[wins['pnl'].idxmax(),'date']})")
    print(f"  Largest loss:        ${loses['pnl'].min():,.1f}  ({loses.loc[loses['pnl'].idxmin(),'date']})")
    print(f"  Reward/risk:         {abs(wins['pnl'].mean()/loses['pnl'].mean()):.1f}x")
    print(f"  Avg pts (winner):    {wins['pts'].mean():.1f}")
    print(f"  Avg pts (loser):     {loses['pts'].mean():.1f}")
    print(f"\n  Worst 5:")
    print(base.nsmallest(5,"pnl")[["date","direction","gap_pct","exit_reason","pts","pnl"]].to_string(index=False))
    print(f"\n  Best 5:")
    print(base.nlargest(5,"pnl")[["date","direction","gap_pct","exit_reason","pts","pnl"]].to_string(index=False))


if __name__ == "__main__":
    main()
