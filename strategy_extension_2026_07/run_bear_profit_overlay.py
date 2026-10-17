"""Causal additive and conflict-free NQ short overlays on frozen long systems."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
BEAR = ROOT / "bear_momentum_2026_07"
sys.path[:0] = [str(HERE), str(BEAR), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from sweep_correction_capture import gates_for  # noqa: E402
from run_episode_search import simulate  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


PROD = dict(rg.SCFG)
SHORTS = {
    "ret5_D20": ("ret5", 20),
    "ret15_D10": ("ret15", 10),
    "ret35_D20": ("ret35", 20),
}


def short_trades(B, gate, donch=20, k=2.0):
    f = strategy.signal_frame(B, PROD)
    dlow = B["close"].rolling(donch).min().shift(1)
    sig = ((B["close"] < dlow) & f["vol_ok"] & f["session_ok"]
           & f["atr"].notna()).values & gate
    atr=f["atr"].values
    O,H,L,C=(B[c].values for c in ("open","high","low","close"))
    R,ts=B["roll"].values,B.index
    rows=[]; pos=pend=0; e=st=lw=np.nan; ets=None
    for i in range(len(B)):
        if pend and R[i]: pend=0
        if pend:
            pos,pend=1,0; e=lw=O[i]; ets=ts[i]; st=e+k*atr[i-1]
            if np.isnan(st): pos=0
        if pos and R[i]:
            x=C[i-1] if i else O[i]
            rows.append((ets,ts[i],"NQ",e,x,e-x,"roll",-1,1)); pos=0
        if pos:
            if H[i]>=st:
                x=max(st,O[i]); rows.append((ets,ts[i],"NQ",e,x,e-x,
                                               "stop",-1,1)); pos=0
            else:
                lw=min(lw,L[i]); st=min(st,lw+k*atr[i])
        if pos==0 and pend==0 and sig[i] and i>donch: pend=1
    T=pd.DataFrame(rows,columns=["entry_time","exit_time","sym","entry_px",
                                 "exit_px","points","reason","dir","qty"])
    return T[T.entry_time>=START].reset_index(drop=True)


def overlap_rate(shorts, longs):
    intervals = longs[["entry_time","exit_time"]].drop_duplicates().to_records(index=False)
    overlap=[]
    for tr in shorts.itertuples():
        overlap.append(any(le>tr.entry_time and ls<tr.exit_time
                           for ls,le in intervals))
    return sum(overlap)/len(overlap)


def core_priority_short(B, gate, longs, donch=20, k=2.0):
    """Causal overlay that never changes the precomputed core-long path."""
    f = strategy.signal_frame(B, PROD)
    dlow = B["close"].rolling(donch).min().shift(1)
    sig = ((B["close"] < dlow) & f["vol_ok"] & f["session_ok"]
           & f["atr"].notna()).values & gate
    atr=f["atr"].values
    O,H,L,C=(B[c].values for c in ("open","high","low","close"))
    R,ts=B["roll"].values,B.index
    active=np.zeros(len(B),dtype=bool); starts=np.zeros(len(B),dtype=bool)
    for tr in longs[["entry_time","exit_time"]].drop_duplicates().itertuples(index=False):
        j=ts.searchsorted(tr.entry_time); z=ts.searchsorted(tr.exit_time)
        active[j:z]=True
        if j<len(starts): starts[j]=True
    rows=[]; pos=pend=0; e=st=lw=np.nan; ets=None
    for i in range(len(B)):
        if pend and (R[i] or active[i]): pend=0
        if pend:
            pos,pend=1,0; e=lw=O[i]; ets=ts[i]; st=e+k*atr[i-1]
            if np.isnan(st): pos=0
        if pos and starts[i]:
            x=O[i]; rows.append((ets,ts[i],"NQ",e,x,e-x,"core_long",-1,1)); pos=0
        if pos and R[i]:
            x=C[i-1] if i else O[i]
            rows.append((ets,ts[i],"NQ",e,x,e-x,"roll",-1,1)); pos=0
        if pos:
            if H[i]>=st:
                x=max(st,O[i]); rows.append((ets,ts[i],"NQ",e,x,e-x,
                                               "stop",-1,1)); pos=0
            else:
                lw=min(lw,L[i]); st=min(st,lw+k*atr[i])
        if pos==0 and pend==0 and not active[i] and sig[i] and i>donch:
            pend=1
    T=pd.DataFrame(rows,columns=["entry_time","exit_time","sym","entry_px",
                                 "exit_px","points","reason","dir","qty"])
    return T[T.entry_time>=START].reset_index(drop=True)


def stats(p):
    eq=p.cumsum(); d=eq-eq.cummax().clip(lower=0); y=p.groupby(p.index.year).sum()
    return p.sum(),d.min(),y.min(),p[p.index.year<2026].sum()


def run():
    rg.START=START
    bars={s:rg.load_60m(s) for s in rg.MULT}; nq=bars["NQ"]
    daily={s:B["close"].resample("1D").last().dropna() for s,B in bars.items()}
    t0,t1=min(B.index.min() for B in bars.values()),max(B.index.max() for B in bars.values())
    policy=weekly_policy(daily,35,t0,t1)["baseline"]
    original_cfg,original_signal=rg.SCFG,strategy.signal_frame
    long_trades={}; long_pnl={}
    try:
        long_cfgs={"production":dict(PROD)} | {
            f"D{dn}K7":dict(PROD,donchian_n=dn,k_trail=7.0,
                             vol_gate_q=.925,session_block_et=(2,7))
            for dn in (8,10,15)}
        for name,cfg in long_cfgs.items():
            rg.SCFG=cfg
            frames={id(B):original_signal(B,cfg) for B in bars.values()}
            strategy.signal_frame=lambda B,ignored:frames[id(B)]
            T=pd.concat([simulate(B,sym,policy) for sym,B in bars.items()]) \
                .sort_values("exit_time").reset_index(drop=True)
            long_trades[name]=T; long_pnl[name]=hourly_mtm(T,bars,rg.FR)

        rg.SCFG=dict(PROD); strategy.signal_frame=original_signal
        gates=gates_for(nq); short_sets={}
        for name,(gate,donch) in SHORTS.items():
            short_sets[name]=short_trades(nq,gates[gate],donch)

        print(f"{'long':<11}{'short':<12}{'mode':<6}{'x':>3}{'net':>11}"
              f"{'maxDD':>11}{'worst':>10}{'ex26':>11}{'overlap':>10}")
        for lname,L in long_trades.items():
            nq_long=L.query("sym=='NQ'")
            for sname,S in short_sets.items():
                gate,donch=SHORTS[sname]
                causal=core_priority_short(nq,gates[gate],nq_long,donch)
                overlap=overlap_rate(S,nq_long)
                for mode,source,ov in (("naive",S,overlap),
                                       ("core",causal,overlap)):
                    for qty in (1,3,5):
                        X=source.copy(); X["qty"]=qty
                        sp=hourly_mtm(X,{"NQ":nq},rg.FR)
                        combo=pd.concat([long_pnl[lname],sp],axis=1).fillna(0).sum(axis=1).sort_index()
                        m=stats(combo)
                        print(f"{lname:<11}{sname:<12}{mode:<6}{qty:>3}"
                              f"{m[0]:>11,.0f}{m[1]:>11,.0f}{m[2]:>10,.0f}"
                              f"{m[3]:>11,.0f}{ov*100:>9.1f}%")
    finally:
        rg.SCFG,strategy.signal_frame=original_cfg,original_signal


if __name__=="__main__": run()
