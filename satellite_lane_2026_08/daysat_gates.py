"""Combined-book gates for the day-anchored candidates."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
exec(open(HERE / "run_daysat.py").read().split("BASE = dict")[0])

# verified lane-1 core (satellite off)
core = []
for sym in bars:
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    T = rg.simulate_weighted(bars[sym], sym, wq)
    T = T[T["qty"] > 0].copy()
    T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
    core.append(T)
CORE = pd.concat(core)
assert abs(CORE["pnl"].sum() - 57138.47) < 1


def daily_mtm_dd(all_trades, lo="2011-01-01", hi="2025-12-31"):
    days = pd.date_range(lo, hi, freq="D", tz="UTC")
    eq = pd.Series(0.0, index=days)
    for sym in bars:
        T = all_trades[all_trades["sym"] == sym]
        dc = daily[sym].reindex(days, method="ffill")
        for tr in T.itertuples():
            mult, q = rg.MULT[sym], tr.qty
            live = ((days >= tr.entry_time.normalize())
                    & (days < tr.exit_time.normalize()))
            eq[live] += (dc[live] - tr.entry_px) * mult * q
            eq[days >= tr.exit_time.normalize()] += (
                (tr.exit_px - tr.entry_px) * mult * q - rg.FR * q)
    return float((eq - eq.cummax()).min())


base_t = CORE[(CORE["exit_time"] >= "2011-01-01")
              & (CORE["exit_time"] < "2026-01-01")]
bp = pd.Series(base_t["pnl"].values, index=pd.DatetimeIndex(base_t["exit_time"])).sort_index()
bdd = daily_mtm_dd(base_t)
byr = bp.groupby(bp.index.year).sum()
print(f"BASELINE  total ${bp.sum():,.0f} maxDD ${bdd:,.0f} "
      f"ratio {bp.sum()/abs(bdd):.2f} worst ${byr.min():,.0f} "
      f"halves {bp[:'2017'].sum():,.0f}/{bp['2018':].sum():,.0f}")

for exit_kind in ("trail1", "trail2"):
    S = pd.concat([day_sat(s, "day9", exit_kind) for s in bars])
    S["pnl"] = (S["points"] * S["sym"].map(rg.MULT) - rg.FR) * S["qty"]
    S = S[(S["exit_time"] >= "2011-01-01") & (S["exit_time"] < "2026-01-01")]
    comb = pd.concat([base_t, S])
    cp = pd.Series(comb["pnl"].values, index=pd.DatetimeIndex(comb["exit_time"])).sort_index()
    cdd = daily_mtm_dd(comb)
    cyr = cp.groupby(cp.index.year).sum()
    d = cp.sum() - bp.sum()
    gates = (d > 0 and cp.sum()/abs(cdd) >= bp.sum()/abs(bdd)
             and cyr.min() >= byr.min() - 500)
    print(f"day9_{exit_kind}: total ${cp.sum():,.0f} (Δ{d:+,.0f}) "
          f"maxDD ${cdd:,.0f} ratio {cp.sum()/abs(cdd):.2f} "
          f"worst ${cyr.min():,.0f}({cyr.idxmin()}) "
          f"halves {cp[:'2017'].sum():,.0f}/{cp['2018':].sum():,.0f} "
          f"{'PASS' if gates else 'fail'}")
