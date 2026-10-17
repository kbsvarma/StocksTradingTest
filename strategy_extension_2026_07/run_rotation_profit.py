"""Profit-first rotation grid under the frozen K7 signal plateau."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(ROOT/"trend_bot"),str(ROOT/"trend_bot"/"research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from rotation import monday_of_week, trailing_returns, weights_for  # noqa: E402
from run_episode_search import simulate  # noqa: E402
from run_policy_search import START, hourly_mtm  # noqa: E402


PROD=dict(rg.SCFG)


def policy_for(daily,lookback,mode,spread,t0,t1):
    rows={}
    for mon in pd.date_range(monday_of_week(t0),t1,freq="W-MON",tz="UTC"):
        rets=trailing_returns(daily,mon,lookback)
        w=weights_for(rets,mode,spread,3)["weights"]
        rows[mon]={f"L_{s}":int(w[s]) for s in rets}
    return pd.DataFrame(rows).T.sort_index()


def run():
    rg.START=START
    bars={s:rg.load_60m(s) for s in rg.MULT}
    daily={s:B["close"].resample("1D").last().dropna() for s,B in bars.items()}
    t0,t1=min(B.index.min() for B in bars.values()),max(B.index.max() for B in bars.values())
    policy_specs=[]
    for lb in (21,35,42,55,84):
        policy_specs.append((lb,"defensive",1.0,
                             policy_for(daily,lb,"defensive",1.0,t0,t1)))
        for mode in ("aggressive","default"):
            for spread in (0,.5,1,1.5,2):
                policy_specs.append((lb,mode,spread,
                                     policy_for(daily,lb,mode,spread,t0,t1)))
    original_cfg,original_signal=rg.SCFG,strategy.signal_frame
    rows=[]
    try:
        for dn in (8,10,15):
            cfg=dict(PROD,donchian_n=dn,k_trail=7.0,
                     vol_gate_q=.925,session_block_et=(2,7))
            rg.SCFG=cfg
            frames={id(B):original_signal(B,cfg) for B in bars.values()}
            strategy.signal_frame=lambda B,ignored:frames[id(B)]
            for lb,mode,spread,policy in policy_specs:
                T=pd.concat([simulate(B,sym,policy) for sym,B in bars.items()]) \
                    .sort_values("exit_time").reset_index(drop=True)
                p=hourly_mtm(T,bars,rg.FR); eq=p.cumsum(); dd=eq-eq.cummax().clip(lower=0)
                y=p.groupby(p.index.year).sum(); ct=int(T.qty.sum()); gross=p.sum()+rg.FR*ct
                rows.append({"cell":f"D{dn}L{lb}{mode[0].upper()}S{spread:g}",
                             "net":p.sum(),"dd":dd.min(),"worst":y.min(),
                             "ex26":p[p.index.year<2026].sum(),
                             "p7":gross-7*ct,"p14":gross-14*ct,
                             "a":p[p.index.year<=2015].sum(),
                             "b":p[(p.index.year>=2016)&(p.index.year<=2020)].sum(),
                             "c":p[p.index.year>=2021].sum(),"ct":ct})
    finally:
        rg.SCFG,strategy.signal_frame=original_cfg,original_signal
    R=pd.DataFrame(rows).sort_values("net",ascending=False)
    print(f"{'cell':<17}{'net':>11}{'maxDD':>11}{'worst':>10}{'ex26':>11}"
          f"{'$7':>11}{'$14':>11}{'11-15':>11}{'16-20':>11}"
          f"{'21-end':>11}{'ct':>9}")
    for r in R.head(60).itertuples():
        print(f"{r.cell:<17}{r.net:>11,.0f}{r.dd:>11,.0f}{r.worst:>10,.0f}"
              f"{r.ex26:>11,.0f}{r.p7:>11,.0f}{r.p14:>11,.0f}"
              f"{r.a:>11,.0f}{r.b:>11,.0f}{r.c:>11,.0f}{r.ct:>9,.0f}")


if __name__=="__main__":run()
