import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine_v as ev
rg = ev.rg
TB = Path(__file__).resolve().parent.parent / "trend_bot"
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
W = rg.weekly_weights(daily, 35, "aggressive300", rg.START,
                      max(b.index.max() for b in bars.values()))
FEATS = {s: ev.precompute(bars[s], rg.SCFG) for s in bars}
WQ = {s: (lambda ts, s=s: int(W.loc[monday_of_week(ts), s])
          if monday_of_week(ts) in W.index else 1) for s in bars}
INF = 10**9


def run(h1=None, h2=None):
    out = []
    for sym in bars:
        T = ev.simulate_v(bars[sym], sym, WQ[sym], FEATS[sym],
                          h1=h1, h2=h2)
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        out.append(T)
    A = pd.concat(out)
    A = A[A["qty"] > 0]
    pnl = pd.Series(A["pnl"].values,
                    index=pd.DatetimeIndex(A["exit_time"])).sort_index()
    P = pnl["2011":"2025"]
    eq = P.groupby(P.index.normalize()).sum().cumsum()
    dd = float((eq - eq.cummax()).min())
    yearly = P.groupby(P.index.year).sum()
    return dict(total=round(float(P.sum())), maxDD=round(dd),
                ratio=round(float(P.sum()) / abs(dd), 2) if dd else np.nan,
                worst_yr=round(float(yearly.min())),
                trades=int(len(P)),
                h1_=round(float(P["2011":"2017"].sum())),
                h2_=round(float(P["2018":"2025"].sum())))


rows = []
base = run()
rows.append(dict(cell="BASELINE", **base))
print("BASELINE", base, flush=True)

cells = []
for k in (5, 10, 15):
    for n in (40, 80, INF):
        cells.append((f"H1A_k{k}_n{'inf' if n == INF else n}",
                      dict(kind="A", k_re=k, n=n), None))
for m in (0.0, 0.5, 1.0):
    for n in (40, 80):
        cells.append((f"H1B_m{m}_n{n}", dict(kind="B", m=m, n=n), None))
for n in (40, 80):
    cells.append((f"H1C_n{n}", dict(kind="C", k_re=10, m=0.5, n=n), None))
H1SUB = dict(kind="A", k_re=10, n=80)
for det in ("ER", "ADX", "WIDTH", "WHIP"):
    for mode in ("M1", "M2", "M3", "M4"):
        cells.append((f"H2_{det}_{mode}", None,
                      dict(det=det, mode=mode, pctl=0.30, h1sub=H1SUB)))

for name, h1, h2 in cells:
    r = run(h1=h1, h2=h2)
    r["d_total"] = r["total"] - base["total"]
    gate = (r["d_total"] > 0 and r["ratio"] >= base["ratio"]
            and r["worst_yr"] >= base["worst_yr"] - 500)
    r["gate"] = "PASS" if gate else ""
    rows.append(dict(cell=name, **r))
    print(f"{name:18} total {r['total']:>7} (Δ{r['d_total']:>+6}) "
          f"ratio {r['ratio']:>5} worst {r['worst_yr']:>6} "
          f"trades {r['trades']:>5} halves {r['h1_']}/{r['h2_']} {r['gate']}",
          flush=True)

pd.DataFrame(rows).to_csv("cells.csv", index=False)
print("\nDONE")
