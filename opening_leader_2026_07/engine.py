"""Opening Leader Continuation — backtest engine per PREREG (zero tuning).

PRIMARY: strongest market-relative gap-up name at 09:35, entry 09:36 open,
stop = first-5-min low (stop-first, gap-honest), exit 10:25 close.
Costs: half-spread 3 bps (month's top-30 dv) / 5 bps else, each side,
+ $0.005/sh commission each side at $10,000 notional.
Headline: 2018-06 .. 2026-04. Generation window (2026-05+) reported apart.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"

U = pd.read_parquet(OUT / "universe.parquet")
panel = pd.read_parquet(OUT / "panel_daily.parquet")
idmap = pd.concat([pd.read_parquet(f) for f in
                   sorted(OUT.glob("idmap_*.parquet"))], ignore_index=True)
mins = pd.concat([pd.read_parquet(f) for f in
                  sorted(OUT.glob("minutes_*.parquet"))], ignore_index=True)

mins["ts"] = pd.to_datetime(mins["ts_event"], utc=True)
mins["et"] = mins["ts"].dt.tz_convert("America/New_York")
mins["hm"] = mins["et"].dt.strftime("%H:%M")
mins = mins[(mins["hm"] >= "09:30") & (mins["hm"] <= "10:29")]
mins = mins.merge(idmap[["day", "instrument_id", "raw_symbol"]],
                  on=["day", "instrument_id"], how="left")

# prior close per (symbol, day): previous trading day's daily close
panel = panel.sort_values("day")
panel["prev_close"] = panel.groupby("raw_symbol")["close"].shift(1)
prev_close = panel.set_index(["day", "raw_symbol"])["prev_close"]

mega = {(r.month, r.symbol) for r in U[U["mega"]].itertuples()}
uni = {m: set(g["symbol"]) for m, g in U.groupby("month")}

integrity = {"no_0930": 0, "no_0934": 0, "no_prev": 0, "no_entry_bar": 0}
rows = []
for day, dblk in mins.groupby("day"):
    month = day[:7]
    if month not in uni:
        continue
    cands = []
    for sym, sblk in dblk.groupby("raw_symbol"):
        if sym not in uni[month]:
            continue
        bars = sblk.set_index("hm")
        if "09:30" not in bars.index:
            integrity["no_0930"] += 1
            continue
        if "09:34" not in bars.index:
            integrity["no_0934"] += 1
            continue
        pc = prev_close.get((day, sym), np.nan)
        if not np.isfinite(pc) or pc <= 0:
            integrity["no_prev"] += 1
            continue
        o930 = float(bars.at["09:30", "open"])
        p935 = float(bars.at["09:34", "close"])
        first5 = bars.loc["09:30":"09:34"]
        cands.append({"sym": sym, "gap": o930 / pc - 1,
                      "r5": p935 / o930 - 1,
                      "stop": float(first5["low"].min()),
                      "bars": bars})
    if not cands:
        continue
    med = float(np.median([c["r5"] for c in cands]))
    live = [dict(c, strength=c["r5"] - med) for c in cands
            if c["gap"] > 0 and c["r5"] > 0]
    if not live:
        continue
    pick = max(live, key=lambda c: c["strength"])
    bars = pick["bars"]
    if "09:36" not in bars.index:
        integrity["no_entry_bar"] += 1
        continue
    entry = float(bars.at["09:36", "open"])
    stop = pick["stop"]
    exit_px, reason = None, "time"
    walk = bars.loc["09:36":"10:25"]
    for hm, b in walk.iterrows():
        if float(b["low"]) <= stop:
            exit_px = min(stop, float(b["open"]))
            reason = "stop"
            break
    if exit_px is None:
        if "10:25" in bars.index:
            exit_px = float(bars.at["10:25", "close"])
        else:                       # halted/no prints: last bar <= 10:25
            exit_px = float(walk["close"].iloc[-1])
            reason = "last_bar"
    hs = 0.0003 if (month, pick["sym"]) in mega else 0.0005
    gross = exit_px / entry - 1
    for mult, tag in ((1, "net"), (2, "net2x")):
        buy = entry * (1 + hs * mult)
        sell = exit_px * (1 - hs * mult)
        shares = 10000.0 / buy
        pnl = shares * (sell - buy) - 2 * shares * 0.005
        if tag == "net":
            net = pnl
        else:
            net2x = pnl
    rows.append({"day": day, "sym": pick["sym"], "entry": entry,
                 "exit": exit_px, "reason": reason, "gross": gross,
                 "net_usd": net, "net2x_usd": net2x,
                 "gap": pick["gap"], "r5": pick["r5"]})

T = pd.DataFrame(rows).sort_values("day")
T.to_csv(BASE / "trades_primary.csv", index=False)
print("integrity:", integrity)
print(f"trade-days: {len(T)} of {mins['day'].nunique()} sessions")

H = T[T["day"] < "2026-05-01"]        # confirmatory headline
G = T[T["day"] >= "2026-05-01"]       # hypothesis-generation window


def stats(X, col="net_usd"):
    if not len(X):
        return "n=0"
    m, s = X[col].mean(), X[col].std(ddof=1)
    t = m / (s / np.sqrt(len(X))) if s > 0 else np.nan
    wr = (X[col] > 0).mean()
    return (f"n={len(X)} mean=${m:,.2f}/trade t={t:.2f} WR={wr:.1%} "
            f"total=${X[col].sum():,.0f} stops={(X['reason'] == 'stop').mean():.0%}")


print("\n=== PRIMARY, headline 2018-06..2026-04 ===")
print("  net   :", stats(H))
print("  net2x :", stats(H, "net2x_usd"))
print("  gross bps/trade:", f"{H['gross'].mean() * 1e4:,.1f}")
print("\nper-year net totals:")
yr = H.assign(y=H["day"].str[:4]).groupby("y")["net_usd"].agg(["sum", "count", "mean"])
print(yr.round(2).to_string())
print("\n=== generation window 2026-05+ (context only) ===")
print("  net   :", stats(G))
g1 = (H.assign(y=H["day"].str[:4]).groupby("y")["net_usd"].sum() > 0)
print(f"\nGATES: t>=2.5? | pos years >=5/8? ({int(g1[g1.index <= '2025'].sum())}/8) "
      "| net2x mean>0?")
