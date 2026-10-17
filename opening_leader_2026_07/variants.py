"""Secondary cells S1 (09:40 entry / 10:30 exit) and S5 (no stop) — context
only per PREREG; cannot rescue the failed primary."""
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
U = pd.read_parquet(OUT / "universe.parquet")
panel = pd.read_parquet(OUT / "panel_daily.parquet")
idmap = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("idmap_*.parquet"))], ignore_index=True)
mins = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("minutes_*.parquet"))], ignore_index=True)
mins["ts"] = pd.to_datetime(mins["ts_event"], utc=True)
mins["hm"] = mins["ts"].dt.tz_convert("America/New_York").dt.strftime("%H:%M")
mins = mins[(mins["hm"] >= "09:30") & (mins["hm"] <= "10:31")]
mins = mins.merge(idmap[["day", "instrument_id", "raw_symbol"]],
                  on=["day", "instrument_id"], how="left")
panel = panel.sort_values("day")
panel["prev_close"] = panel.groupby("raw_symbol")["close"].shift(1)
prev_close = panel.set_index(["day", "raw_symbol"])["prev_close"]
mega = {(r.month, r.symbol) for r in U[U["mega"]].itertuples()}
uni = {m: set(g["symbol"]) for m, g in U.groupby("month")}


def run(entry_hm, exit_hm, use_stop):
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
            if "09:30" not in bars.index or "09:34" not in bars.index:
                continue
            pc = prev_close.get((day, sym), np.nan)
            if not np.isfinite(pc) or pc <= 0:
                continue
            o930 = float(bars.at["09:30", "open"])
            p935 = float(bars.at["09:34", "close"])
            cands.append({"sym": sym, "gap": o930 / pc - 1,
                          "r5": p935 / o930 - 1,
                          "stop": float(bars.loc["09:30":"09:34", "low"].min()),
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
        if entry_hm not in bars.index:
            continue
        entry = float(bars.at[entry_hm, "open"])
        exit_px, reason = None, "time"
        walk = bars.loc[entry_hm:exit_hm]
        if use_stop:
            for hm, b in walk.iterrows():
                if float(b["low"]) <= pick["stop"]:
                    exit_px = min(pick["stop"], float(b["open"]))
                    reason = "stop"
                    break
        if exit_px is None:
            exit_px = (float(bars.at[exit_hm, "close"])
                       if exit_hm in bars.index
                       else float(walk["close"].iloc[-1]))
        hs = 0.0003 if (month, pick["sym"]) in mega else 0.0005
        buy, sell = entry * (1 + hs), exit_px * (1 - hs)
        sh = 10000.0 / buy
        rows.append({"day": day,
                     "net_usd": sh * (sell - buy) - 2 * sh * 0.005})
    T = pd.DataFrame(rows)
    H = T[T["day"] < "2026-05-01"]
    m, s = H["net_usd"].mean(), H["net_usd"].std(ddof=1)
    t = m / (s / np.sqrt(len(H)))
    return (f"n={len(H)} mean=${m:,.2f} t={t:.2f} "
            f"WR={(H['net_usd']>0).mean():.1%} total=${H['net_usd'].sum():,.0f}")


print("S1 (entry 09:40, exit 10:30, stop on): ", run("09:40", "10:30", True))
print("S5 (entry 09:36, exit 10:25, NO stop): ", run("09:36", "10:25", False))
