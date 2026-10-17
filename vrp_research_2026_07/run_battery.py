"""Run the 36-cell grid per name. IS window only by default (2021-07 -> 2024-12);
--oos runs the locked survivor set on 2025-01 -> 2026-07 (single shot).
Output: data/battery_is.parquet (one row per name-cell with monthly stats).
Benchmark per name: buy-and-hold 100 shares over the same window.
"""
import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
from vrp_engine import run_wheel

NAMES = ["MARA", "MSTR", "COIN", "SMCI", "RIVN", "HOOD", "SOFI", "PLTR", "NVDA"]
IS_START, IS_END = "2021-07-01", "2024-12-31"
OOS_START, OOS_END = "2025-01-01", "2026-07-02"

def closes_map():
    xn = pd.read_parquet("data/equity_dailies_xnas.parquet")
    out = {}
    for sym, g in xn.groupby("symbol"):
        c = g["close"].copy()
        c.index = c.index.tz_localize(None).normalize()
        out[sym] = c
    return out

def monthly_stats(ledger, closes, start, end, name=None):
    if ledger is None or len(ledger) == 0:
        return None
    L = ledger[(ledger.date >= start) & (ledger.date <= end)]
    if len(L) == 0:
        return None
    cash = L.set_index("date")["cash"]
    monthly = cash.groupby(cash.index.to_period("M")).sum()
    # benchmark: 100 shares B&H, SPLIT-ADJUSTED return applied to initial notional
    c = closes[(closes.index >= start) & (closes.index <= end)].dropna()
    r = c.pct_change()
    from vrp_engine import SPLITS
    for sdt, f in SPLITS.get(name, []):
        sdt = pd.Timestamp(sdt)
        hits = r.index[r.index >= sdt]
        if len(hits) and len(c.index[c.index < sdt]):
            j = hits[0]
            prev = c[c.index < sdt].iloc[-1]
            r.loc[j] = c.loc[j] * f / prev - 1.0
    adj_ret = float((1 + r.fillna(0)).prod() - 1)
    bh = c.iloc[0] * 100 * adj_ret
    n_assign = int((L.kind == "ASSIGN_P").sum())
    return dict(total=float(cash.sum()), months=len(monthly),
                med_month=float(monthly.median()), worst_month=float(monthly.min()),
                best_month=float(monthly.max()), pct_pos_months=float((monthly > 0).mean() * 100),
                n_events=len(L), n_assign=n_assign, bh=float(bh))

def main(oos=False):
    closes = closes_map()
    earnings = {k: [pd.Timestamp(x) for x in v]
                for k, v in json.load(open("data/earnings_dates.json")).items()}
    start, end = (pd.Timestamp(OOS_START), pd.Timestamp(OOS_END)) if oos else (pd.Timestamp(IS_START), pd.Timestamp(IS_END))
    rows = []
    grid = [(csp, otm, pt, skipE)
            for csp in (True, False)
            for otm in (0.0, 0.05, 0.10)
            for pt in (None, 0.5, 0.25)
            for skipE in (False, True)]
    if oos:
        # locked survivor cells are read from SURVIVORS_VRP.json
        surv = json.load(open("SURVIVORS_VRP.json"))
        grid = [tuple(x) for x in surv["cells"]]
        grid = [(bool(a), float(b), (None if c is None else float(c)), bool(d)) for a, b, c, d in grid]
    for name in NAMES:
        for (csp, otm, pt, skipE) in grid:
            led = run_wheel(name, closes[name], earnings[name], otm_pct=otm, pt=pt,
                            skip_earnings=skipE, start=str(start.date()), end=str(end.date()),
                            csp_only=csp)
            st = monthly_stats(led, closes[name], start, end, name=name)
            if st is None:
                continue
            st.update(dict(name=name, csp_only=csp, otm=otm,
                           pt=str(pt), skip_earn=skipE))
            rows.append(st)
        print(f"{name} done ({len(rows)} rows so far)", flush=True)
    out = pd.DataFrame(rows)
    fn = "data/battery_oos.parquet" if oos else "data/battery_is.parquet"
    out.to_parquet(fn)
    print(f"wrote {fn}: {len(out)} rows")

if __name__ == "__main__":
    main("--oos" in sys.argv)
