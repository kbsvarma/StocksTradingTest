"""One-table recomputation of the frozen profit/risk options."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
HERE=Path(__file__).resolve().parent
BEAR=ROOT/"bear_momentum_2026_07"
sys.path[:0]=[str(HERE),str(BEAR),str(ROOT/"trend_bot"),str(ROOT/"trend_bot"/"research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from sweep_correction_capture import gates_for  # noqa: E402
from run_bear_profit_overlay import core_priority_short  # noqa: E402
from run_episode_search import simulate  # noqa: E402
from run_policy_search import START,hourly_mtm,weekly_policy  # noqa: E402
from run_profit_max_grid import max_policy,plan_shapes  # noqa: E402


PROD=dict(rg.SCFG)


OPTIONS={
    "production": (20,3,"base",0),
    "D15K7_N3": (15,3,"base",0),
    "D10K7_N3": (10,3,"base",0),
    "D8K7_N3": (8,3,"base",0),
    "D15K7_N3_bear3": (15,3,"base",3),
    "D10K7_N3_bear3": (10,3,"base",3),
    "D8K7_N3_bear5": (8,3,"base",5),
    "D15K7_N12_allin": (15,12,"allin",0),
    "D8K7_N12_convex": (8,12,"convex",0),
    "D15K7_N30_allin": (15,30,"allin",0),
    "D8K7_N30_convex": (8,30,"convex",0),
    "D8K7_N30_allin": (8,30,"allin",0),
    "D15K7_N30_allin_bear5": (15,30,"allin",5),
    "D8K7_N30_convex_bear5": (8,30,"convex",5),
    "D8K7_N30_allin_bear5": (8,30,"allin",5),
}


def long_path(bars,base,dn,n,shape,original_signal):
    cfg=(dict(PROD) if dn==20 and shape=="base" and n==3 else
         dict(PROD,donchian_n=dn,k_trail=7.0,
              vol_gate_q=.925,session_block_et=(2,7)))
    rg.SCFG=cfg
    frames={id(B):original_signal(B,cfg) for B in bars.values()}
    strategy.signal_frame=lambda B,ignored:frames[id(B)]
    policy=base if n==3 else max_policy(base,n)
    kw={}
    if shape=="allin": kw={"loss_session_rearm":True}
    elif shape=="convex": kw={"stages":plan_shapes(n)["convex_ladder"],
                               "loss_session_rearm":True}
    T=pd.concat([simulate(B,sym,policy,**kw) for sym,B in bars.items()]) \
        .sort_values("exit_time").reset_index(drop=True)
    return T,hourly_mtm(T,bars,rg.FR),cfg


def run():
    rg.START=START
    bars={s:rg.load_60m(s) for s in rg.MULT}; nq=bars["NQ"]
    daily={s:B["close"].resample("1D").last().dropna() for s,B in bars.items()}
    t0,t1=min(B.index.min() for B in bars.values()),max(B.index.max() for B in bars.values())
    base=weekly_policy(daily,35,t0,t1)["baseline"]
    gate=gates_for(nq)["ret5"]
    original_cfg,original_signal=rg.SCFG,strategy.signal_frame
    cache={}; rows=[]
    try:
        for label,(dn,n,shape,bearqty) in OPTIONS.items():
            key=(dn,n,shape)
            if key not in cache:
                cache[key]=long_path(bars,base,dn,n,shape,original_signal)
            T,p,cfg=cache[key]
            ct=int(T.qty.sum())
            if bearqty:
                rg.SCFG=dict(PROD); strategy.signal_frame=original_signal
                S=core_priority_short(nq,gate,T.query("sym=='NQ'"),20,2.0)
                S["qty"]=bearqty
                p=pd.concat([p,hourly_mtm(S,{"NQ":nq},rg.FR)],axis=1) \
                    .fillna(0).sum(axis=1).sort_index()
                ct+=int(S.qty.sum())
            eq=p.cumsum(); dd=eq-eq.cummax().clip(lower=0); y=p.groupby(p.index.year).sum()
            gross=p.sum()+rg.FR*ct
            rows.append({"option":label,"net":p.sum(),"annual":p.sum()/15.5,
                         "dd":dd.min(),"worst":y.min(),
                         "ex26":p[p.index.year<2026].sum(),"p7":gross-7*ct,
                         "p14":gross-14*ct,"a":p[p.index.year<=2015].sum(),
                         "b":p[(p.index.year>=2016)&(p.index.year<=2020)].sum(),
                         "c":p[p.index.year>=2021].sum(),"ct":ct,
                         "ratio":p.sum()/abs(dd.min())})
    finally:
        rg.SCFG,strategy.signal_frame=original_cfg,original_signal
    R=pd.DataFrame(rows).sort_values("net",ascending=False)
    baseline=float(R.loc[R.option=="production","net"].iloc[0])
    R["delta"]=R.net-baseline
    print(f"{'option':<29}{'net':>11}{'$/yr':>10}{'delta':>11}{'maxDD':>11}"
          f"{'worst':>10}{'ex26':>11}{'$7':>11}{'$14':>11}"
          f"{'11-15':>11}{'16-20':>11}{'21-end':>11}{'ct':>9}{'P/DD':>8}")
    for r in R.itertuples():
        print(f"{r.option:<29}{r.net:>11,.0f}{r.annual:>10,.0f}{r.delta:>+11,.0f}"
              f"{r.dd:>11,.0f}{r.worst:>10,.0f}{r.ex26:>11,.0f}"
              f"{r.p7:>11,.0f}{r.p14:>11,.0f}{r.a:>11,.0f}"
              f"{r.b:>11,.0f}{r.c:>11,.0f}{r.ct:>9,.0f}{r.ratio:>8.2f}")


if __name__=="__main__":run()
