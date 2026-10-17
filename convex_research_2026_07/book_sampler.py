"""Study 1 — order-book state at breakout signals (bbo-1s sampled windows).

Stratified sample: up to 400 signals/instrument (NQ/ES/YM), spread by year.
Window per signal: [entry-20min, entry+1min] bbo-1s (top-of-book px+sz @1s).
Feature: mean top-of-book imbalance bid_sz/(bid_sz+ask_sz) over the final
15 min before entry (long breakouts: thin ask -> punch-through hypothesis
predicts HIGHER pnl for LOW ask-side share... i.e. high imbalance).
Resumable: appends to book_features.parquet, skips sampled ids.
"""
import os
import sys
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent / "trend_bot"))
sys.path.insert(0, str(BASE.parent / "trend_bot/research"))
import rotation_grid as rg

KEY = os.environ["DATABENTO_API_KEY"]
OUT = BASE / "data/book_features.parquet"
PER_INST = 400

client = db.Historical(KEY)

def sample_trades():
    rows = []
    for sym in ("NQ", "ES", "YM"):
        B = rg.load_60m(sym)
        T = rg.simulate(B, sym)
        T = T[T["entry_time"] >= pd.Timestamp("2016-01-01", tz="UTC")].copy()
        T["sym"] = sym
        T["pnl"] = T["points"] * rg.MULT[sym] - rg.FR
        T["year"] = pd.DatetimeIndex(T["entry_time"]).year
        # stratified: equal per year
        per_year = max(1, PER_INST // T["year"].nunique())
        samp = (T.groupby("year", group_keys=False)
                 .apply(lambda g: g.sample(min(len(g), per_year), random_state=7)))
        rows.append(samp)
    return pd.concat(rows).reset_index(drop=True)

def main():
    trades = sample_trades()
    trades["tid"] = (trades["sym"] + "_"
                     + pd.DatetimeIndex(trades["entry_time"]).strftime("%Y%m%d%H%M"))
    done = set()
    feats = []
    if OUT.exists():
        prev = pd.read_parquet(OUT)
        done = set(prev["tid"])
        feats = [prev]
        print(f"resuming: {len(done)} already sampled")
    todo = trades[~trades["tid"].isin(done)]
    print(f"sampling {len(todo)} signals...")
    buf = []
    for k, (_, r) in enumerate(todo.iterrows()):
        ent = r["entry_time"]
        try:
            d = client.timeseries.get_range(
                dataset="GLBX.MDP3", symbols=[f"{r['sym']}.v.0"],
                stype_in="continuous", schema="bbo-1s",
                start=(ent - pd.Timedelta(minutes=20)).isoformat(),
                end=(ent + pd.Timedelta(minutes=1)).isoformat())
            q = d.to_df()
            if not len(q):
                continue
            w = q[q.index >= ent - pd.Timedelta(minutes=15)]
            bs, asz = w["bid_sz_00"].astype(float), w["ask_sz_00"].astype(float)
            imb = (bs / (bs + asz)).replace([np.inf, -np.inf], np.nan).mean()
            spread_ticks = ((w["ask_px_00"] - w["bid_px_00"])).mean()
            buf.append(dict(tid=r["tid"], sym=r["sym"], entry_time=ent,
                            pnl=r["pnl"], imb=float(imb),
                            ask_sz=float(asz.mean()), bid_sz=float(bs.mean()),
                            spread=float(spread_ticks), n_sec=len(w)))
        except Exception as e:
            buf.append(dict(tid=r["tid"], sym=r["sym"], entry_time=ent,
                            pnl=r["pnl"], imb=np.nan, ask_sz=np.nan,
                            bid_sz=np.nan, spread=np.nan, n_sec=0))
            print(f"  {r['tid']}: {str(e)[:80]}", flush=True)
        if (k + 1) % 50 == 0:
            allf = pd.concat(feats + [pd.DataFrame(buf)])
            allf.to_parquet(OUT)
            print(f"  {k+1}/{len(todo)} sampled", flush=True)
    allf = pd.concat(feats + [pd.DataFrame(buf)]) if buf else pd.concat(feats)
    allf.to_parquet(OUT)
    print(f"DONE: {len(allf)} features -> {OUT}")

if __name__ == "__main__":
    main()
