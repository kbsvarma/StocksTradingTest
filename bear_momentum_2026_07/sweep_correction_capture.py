"""DECLARED EXPLORATORY SWEEP — correction-capture bear overlay.

Not a prereg single-shot: a mapped grid, judged with multiplicity-aware
rules fixed in advance (2026-07-18, user-approved protocol):
  survivor requires (a) short-total > 0 on NQ AND >= 2 of 3 instruments,
  (b) NQ halves (2011-18 / 2019-26) BOTH positive, (c) worst short-side
  calendar year >= -$1,000 on every instrument (gold-class rent bound),
  (d) PLATEAU: >= 2 gate-axis neighbors also pass (no spike cells).
Best possible outcome = shadow slot. ALL gates lagged to D-1 close.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trend_bot"))
sys.path.insert(0, str(ROOT / "trend_bot" / "research"))

import rotation_grid as rg                     # noqa: E402
import strategy                                # noqa: E402

rg.START = pd.Timestamp("2011-01-01", tz="UTC")

RET_LB = [5, 10, 15, 21, 35, 63]
DD_GATES = [(3, 20), (5, 20), (3, 60), (5, 60), (8, 60)]
DONCH_N = [10, 20]
K_SHORT = [2.0, 3.0]
EXITS = ["trail", "tb80"]


def gates_for(B):
    d = B["close"].resample("1D").last().dropna()
    out = {}
    for lb in RET_LB:
        g = (d.pct_change(lb) < 0).shift(1)          # D-1 close, honest
        out[f"ret{lb}"] = g.reindex(B.index.normalize()).fillna(False).values
    for x, n in DD_GATES:
        dd = (d / d.rolling(n).max() - 1 < -x / 100).shift(1)
        out[f"dd{x}pct{n}d"] = dd.reindex(
            B.index.normalize()).fillna(False).values
    return out


def short_only(B, gate, donch_n, k, exit_style):
    f = strategy.signal_frame(B, rg.SCFG)          # vol+session gates
    dlow = B["close"].rolling(donch_n).min().shift(1)
    sig = ((B["close"] < dlow) & f["vol_ok"] & f["session_ok"]
           & f["atr"].notna()).values & gate
    atr = f["atr"].values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R, ts = B["roll"].values, B.index
    trades = []
    pos = pend = held = 0
    e = st = lw = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend and R[i]:
            pend = 0
        if pend:
            pos, pend, held = 1, 0, 0
            e, lw, e_ts = O[i], O[i], ts[i]
            st = e + k * atr[i - 1]
            if np.isnan(st):
                pos = 0
        if pos and R[i]:
            x = C[i - 1] if i else O[i]
            trades.append((e_ts, ts[i], e - x))
            pos = 0
        if pos:
            held += 1
            if H[i] >= st:
                x = max(st, O[i])
                trades.append((e_ts, ts[i], e - x))
                pos = 0
            elif exit_style == "tb80" and held >= 80:
                trades.append((e_ts, ts[i], e - C[i]))
                pos = 0
            else:
                lw = min(lw, L[i])
                st = min(st, lw + k * atr[i])
        if pos == 0 and pend == 0 and sig[i] and i > donch_n:
            pend = 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "points"])
    return T[T["entry_time"] >= rg.START].reset_index(drop=True)


def main():
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    gate_maps = {s: gates_for(bars[s]) for s in rg.MULT}
    gate_names = list(gate_maps["NQ"].keys())
    rows = []
    for gname in gate_names:
        for dn in DONCH_N:
            for k in K_SHORT:
                for ex in EXITS:
                    cell = dict(gate=gname, donch=dn, k=k, exit=ex)
                    ok_syms = 0
                    for sym in rg.MULT:
                        T = short_only(bars[sym], gate_maps[sym][gname],
                                       dn, k, ex)
                        pnl = pd.Series(
                            T["points"].values * rg.MULT[sym] - rg.FR,
                            index=pd.DatetimeIndex(T["exit_time"]))
                        y = pnl.groupby(pnl.index.year).sum()
                        cell[f"{sym}_tot"] = float(pnl.sum())
                        cell[f"{sym}_wy"] = float(y.min()) if len(y) else 0
                        cell[f"{sym}_n"] = len(T)
                        if sym == "NQ":
                            h1 = float(pnl[pnl.index.year <= 2018].sum())
                            h2 = float(pnl[pnl.index.year >= 2019].sum())
                            cell["NQ_h1"], cell["NQ_h2"] = h1, h2
                        if float(pnl.sum()) > 0:
                            ok_syms += 1
                    cell["pass"] = (
                        cell["NQ_tot"] > 0 and ok_syms >= 2
                        and cell["NQ_h1"] > 0 and cell["NQ_h2"] > 0
                        and all(cell[f"{s}_wy"] >= -1000 for s in rg.MULT))
                    rows.append(cell)
    G = pd.DataFrame(rows)
    G.to_csv(Path(__file__).parent / "sweep_grid.csv", index=False)
    n_pass = int(G["pass"].sum())
    print(f"grid: {len(G)} cells | raw passes (pre-plateau): {n_pass} "
          f"({n_pass/len(G)*100:.0f}%)")
    # plateau on the gate axis within (donch, k, exit) slices
    order = gate_names
    survivors = []
    for _, r in G[G["pass"]].iterrows():
        gi = order.index(r["gate"])
        nb = [order[j] for j in (gi - 1, gi + 1) if 0 <= j < len(order)]
        n_ok = sum(bool(G[(G["gate"] == g) & (G["donch"] == r["donch"])
                         & (G["k"] == r["k"]) & (G["exit"] == r["exit"])
                         ]["pass"].iloc[0]) for g in nb)
        if n_ok >= min(2, len(nb)):
            survivors.append(r)
    print(f"PLATEAU SURVIVORS: {len(survivors)}")
    for r in survivors:
        print(f"  {r['gate']:<10} N{int(r['donch'])} K{r['k']} "
              f"{r['exit']:<6} NQ ${r['NQ_tot']:>7,.0f} "
              f"(h1 {r['NQ_h1']:>6,.0f}/h2 {r['NQ_h2']:>6,.0f}) "
              f"ES ${r['ES_tot']:>7,.0f} YM ${r['YM_tot']:>7,.0f} "
              f"worst({r['NQ_wy']:,.0f}/{r['ES_wy']:,.0f}/{r['YM_wy']:,.0f})")


if __name__ == "__main__":
    main()
