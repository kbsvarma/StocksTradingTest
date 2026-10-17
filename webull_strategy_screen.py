"""
Strategy screen — exact same simulation logic as mes_orb_bot/backtest.py
Tests long-only ORB configs on MES 1-min data 2019-2026.
Translates to SPY ETF dollar sizing for Webull deployment.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MES_1MIN  = Path("mes_orb_bot/data/mes_1min.csv")
VIX_FILE  = Path("spx_spread_bot/backtests/ten_year/market_data/vix.csv")
MULTIPLIER  = 5.0   # MES: $5 per point
COMMISSION  = 5.50  # round-trip

# ── Load ──────────────────────────────────────────────────────────────────────
print("Loading MES 1-min data...")
raw = pd.read_csv(MES_1MIN, parse_dates=["ts_event"])
raw = raw.rename(columns={"ts_event": "ts"})
raw["ts"] = pd.to_datetime(raw["ts"], utc=True).dt.tz_convert("America/New_York")
raw["_date"] = raw["ts"].dt.date
raw["_min"]  = raw["ts"].dt.hour * 60 + raw["ts"].dt.minute
raw["_dow"]  = raw["ts"].dt.day_name()
rth = raw[(raw["_min"] >= 9*60+30) & (raw["_min"] <= 15*60+59)].copy()

vix_df  = pd.read_csv(VIX_FILE, parse_dates=["date"])
vix_df["date"] = vix_df["date"].dt.date
vix_map = dict(zip(vix_df["date"], vix_df["close"]))

daily_close = rth.groupby("_date")["close"].last()
dates_sorted = sorted(daily_close.index)
prior_close_map = {dates_sorted[i]: float(daily_close[dates_sorted[i-1]])
                   for i in range(1, len(dates_sorted))}

# ── DayCache ──────────────────────────────────────────────────────────────────
@dataclass
class DayCache:
    date:      object
    rng_high:  float
    rng_low:   float
    width:     float
    vix:       float
    dow:       str
    skip:      str
    times:     np.ndarray = field(default_factory=lambda: np.array([]))
    opens:     np.ndarray = field(default_factory=lambda: np.array([]))
    highs:     np.ndarray = field(default_factory=lambda: np.array([]))
    lows:      np.ndarray = field(default_factory=lambda: np.array([]))
    closes:    np.ndarray = field(default_factory=lambda: np.array([]))
    prior_close: float = 0.0
    day_open:    float = 0.0

print("Pre-computing day caches...")
caches = []
for d in sorted(rth["_date"].unique()):
    day = rth[rth["_date"] == d]
    vix = vix_map.get(d, 999)
    dow = day["_dow"].iloc[0]
    pc  = prior_close_map.get(d, 0.0)

    # Opening range: 09:30–09:44
    orb_bars = day[day["_min"] <= 9*60+44]
    if len(orb_bars) < 5:
        caches.append(DayCache(d, 0, 0, 0, vix, dow, "THIN_RANGE"))
        continue

    rh = float(orb_bars["high"].max())
    rl = float(orb_bars["low"].min())
    w  = round(rh - rl, 4)
    do = float(day[day["_min"] == 9*60+30]["open"].iloc[0]) if len(day[day["_min"] == 9*60+30]) else 0.0

    post = day[day["_min"] >= 9*60+45]
    if post.empty:
        caches.append(DayCache(d, rh, rl, w, vix, dow, "NO_POST"))
        continue

    caches.append(DayCache(
        date=d, rng_high=rh, rng_low=rl, width=w, vix=vix, dow=dow, skip="",
        times=post["_min"].values.astype(np.float32),
        opens=post["open"].values.astype(np.float32),
        highs=post["high"].values.astype(np.float32),
        lows=post["low"].values.astype(np.float32),
        closes=post["close"].values.astype(np.float32),
        prior_close=pc, day_open=do,
    ))

print(f"  {len(caches)} days cached ({sum(1 for c in caches if not c.skip)} tradeable)")

# ── Simulate ──────────────────────────────────────────────────────────────────
def run(label, vix_max=25, min_w=5, max_w=25, target_mult=3.0,
        hard_close_min=15*60+15, entry_cutoff_min=15*60+30,
        long_only=True, skip_thu=True, trend_filter=True):

    results = []
    for c in caches:
        if c.skip or c.vix > vix_max:
            continue
        if skip_thu and c.dow == "Thursday":
            continue
        if c.width < min_w or c.width > max_w:
            continue
        if trend_filter and c.prior_close > 0:
            trend_up = c.day_open >= c.prior_close
            if long_only and not trend_up:
                continue

        times, opens, highs, lows, closes = c.times, c.opens, c.highs, c.lows, c.closes
        mask = times <= entry_cutoff_min
        cl = closes[mask]

        long_hits  = np.where(cl > c.rng_high)[0]
        short_hits = np.where(cl < c.rng_low)[0]

        has_long  = len(long_hits) > 0
        has_short = len(short_hits) > 0 and not long_only

        if not has_long and not has_short:
            continue

        li = long_hits[0]  if has_long  else len(cl)
        si = short_hits[0] if has_short else len(cl)
        direction  = "LONG" if li <= si else "SHORT"
        signal_idx = li if direction == "LONG" else si

        entry_idx = signal_idx + 1
        if entry_idx >= len(opens):
            continue

        entry = float(opens[entry_idx])
        stop   = c.rng_low  if direction == "LONG" else c.rng_high
        target = entry + target_mult * c.width if direction == "LONG" \
                 else entry - target_mult * c.width

        t_lo = lows[entry_idx:]
        t_hi = highs[entry_idx:]
        t_cl = closes[entry_idx:]
        t_t  = times[entry_idx:]

        stop_hit   = t_lo <= stop   if direction == "LONG" else t_hi >= stop
        target_hit = t_hi >= target if direction == "LONG" else t_lo <= target
        hc_hit     = t_t >= hard_close_min
        any_hit    = stop_hit | target_hit | hc_hit

        if not np.any(any_hit):
            exit_price = float(t_cl[-1]) if len(t_cl) else entry
            reason = "HC"
        else:
            i = int(np.argmax(any_hit))
            if stop_hit[i]:
                exit_price, reason = stop, "STOP"
            elif target_hit[i]:
                exit_price, reason = target, "TARGET"
            else:
                exit_price, reason = float(t_cl[i]), "HC"

        pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        pnl_net = pnl_pts * MULTIPLIER - COMMISSION

        results.append({
            "date": c.date, "year": c.date.year,
            "direction": direction, "reason": reason,
            "pnl_pts": round(pnl_pts, 2), "pnl_net": round(pnl_net, 2),
            "vix": c.vix, "width": c.width,
        })

    if not results:
        return None

    res = pd.DataFrame(results)
    res["win"] = res["pnl_net"] > 0
    cum = res["pnl_net"].cumsum()
    max_dd = (cum - cum.cummax()).min()
    sharpe = res["pnl_net"].mean() / res["pnl_net"].std() * np.sqrt(252) if res["pnl_net"].std() > 0 else 0

    oos = res[res["year"] >= 2023]
    trades_per_yr = len(oos) / max(1, len(oos["year"].unique()))

    return {
        "label": label,
        "n": len(res), "wr": res["win"].mean()*100,
        "avg": res["pnl_net"].mean(), "total": res["pnl_net"].sum(),
        "max_dd": max_dd, "sharpe": sharpe,
        "oos_avg": oos["pnl_net"].mean() if len(oos) else 0,
        "oos_wr": oos["win"].mean()*100 if len(oos) else 0,
        "oos_n_yr": trades_per_yr,
        "res": res,
    }

# ── Run configs ───────────────────────────────────────────────────────────────
configs = [
    dict(label="A  LO + maxW25 + HC15:15 + noThu + trend",
         long_only=True,  max_w=25, hard_close_min=15*60+15, skip_thu=True,  trend_filter=True),
    dict(label="B  LO + maxW25 + HC15:15 + noThu + no trend",
         long_only=True,  max_w=25, hard_close_min=15*60+15, skip_thu=True,  trend_filter=False),
    dict(label="C  LO + maxW25 + HC15:30 + noThu + trend",
         long_only=True,  max_w=25, hard_close_min=15*60+30, skip_thu=True,  trend_filter=True),
    dict(label="D  LO + maxW30 + HC15:15 + noThu + trend",
         long_only=True,  max_w=30, hard_close_min=15*60+15, skip_thu=True,  trend_filter=True),
    dict(label="E  LO + maxW25 + HC15:15 + allDays + trend",
         long_only=True,  max_w=25, hard_close_min=15*60+15, skip_thu=False, trend_filter=True),
    dict(label="F  LO + maxW25 + HC15:15 + noThu + trend + VIX20",
         long_only=True,  max_w=25, hard_close_min=15*60+15, skip_thu=True,  trend_filter=True, vix_max=20),
    dict(label="G  BOTH dirs + maxW25 + HC15:15 + noThu + trend",
         long_only=False, max_w=25, hard_close_min=15*60+15, skip_thu=True,  trend_filter=True),
    dict(label="H  LO + maxW25 + HC15:15 + noThu + trend + tgt2R",
         long_only=True,  max_w=25, hard_close_min=15*60+15, skip_thu=True,  trend_filter=True, target_mult=2.0),
]

print(f"\n{'='*95}")
print(f"  {'Config':<45} {'N':>5} {'WR%':>5} {'Avg$':>7} {'Total$':>8} {'MaxDD$':>8} {'OOS Avg$':>9} {'Sharpe':>7}")
print(f"  {'-'*93}")

all_r = []
for cfg in configs:
    r = run(**cfg)
    if r:
        all_r.append(r)
        print(f"  {r['label']:<45} {r['n']:>5} {r['wr']:>5.1f} {r['avg']:>7.2f} "
              f"{r['total']:>8,.0f} {r['max_dd']:>8,.0f} {r['oos_avg']:>9.2f} {r['sharpe']:>7.3f}")

best = max(all_r, key=lambda x: x["oos_avg"])
print(f"\n  ★ WINNER: {best['label']}")

print(f"\n{'='*60}")
print(f"  YEAR-BY-YEAR — {best['label']}")
print(f"  {'Year':<8} {'Trades':>7} {'WR%':>6} {'Total$':>9} {'Avg/trade':>11}")
print(f"  {'-'*50}")
for yr, g in best["res"].groupby("year"):
    print(f"  {yr:<8} {len(g):>7} {g['win'].mean()*100:>5.1f}% {g['pnl_net'].sum():>9,.0f} {g['pnl_net'].mean():>11.2f}")

# ── SPY sizing translation ─────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  SPY SIZING — target $50/qualifying trade")
print(f"  (SPY ≈ SPX/10, MES point ≈ 0.1 SPY point)")
print()

oos_avg_pts = best["res"][best["res"]["year"] >= 2023]["pnl_pts"].mean()
print(f"  MES avg profit OOS : {oos_avg_pts:.2f} pts/trade")
print(f"  SPY equivalent     : ${oos_avg_pts/10:.2f}/share/trade")

for risk_target in [200, 400, 600]:
    spy_stop_pts = 3.0   # typical stop distance in SPY points (= orb_width * 1 for stop-at-range-low)
    shares = round(risk_target / spy_stop_pts)
    expected_win = shares * (oos_avg_pts / 10)
    print(f"\n  Risk ${risk_target}/trade → {shares} SPY shares (~${shares*739:,.0f} capital)")
    print(f"    Expected profit/trade (OOS avg) : ${expected_win:.0f}")
    print(f"    Expected trades/year            : ~{best['oos_n_yr']:.0f}")
    print(f"    Expected annual P&L             : ~${expected_win * best['oos_n_yr']:,.0f}")
    print(f"    Expected per calendar day       : ~${expected_win * best['oos_n_yr'] / 252:.0f}")
    print(f"    Max drawdown (scaled)           : ~${best['max_dd'] * risk_target / 400:,.0f}")
