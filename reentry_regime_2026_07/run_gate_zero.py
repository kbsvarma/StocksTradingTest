import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine_v as ev
rg = ev.rg

TB = Path(__file__).resolve().parent.parent / "trend_bot"
sys.path.insert(0, str(TB / "ops"))
from data import BarStore
from rotation import monday_of_week

store = BarStore(TB / "state")
bars, daily = {}, {}
for sym in ("NQ", "ES", "YM"):
    B = store.bars(sym)[["open", "high", "low", "close"]].copy()
    B["roll"] = 0
    bars[sym] = B
    daily[sym] = B["close"].resample("1D").last().dropna()
rg.START = min(b.index.min() for b in bars.values())
t0, t1 = rg.START, max(b.index.max() for b in bars.values())
W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)

ok_all = True
feats = {}
for sym in ("NQ", "ES", "YM"):
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    ref = rg.simulate_weighted(bars[sym], sym, wq)
    feats[sym] = ev.precompute(bars[sym], rg.SCFG)
    got = ev.simulate_v(bars[sym], sym, wq, feats[sym])
    same = (len(ref) == len(got)
            and ref[["entry_time", "exit_time", "points", "qty"]]
                .equals(got[["entry_time", "exit_time", "points", "qty"]]))
    print(f"{sym}: ref {len(ref)} trades, replica {len(got)} — "
          f"{'IDENTICAL' if same else 'MISMATCH'}")
    ok_all &= same
print("GATE ZERO:", "PASS" if ok_all else "FAIL")
