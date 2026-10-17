"""Offline eight-market budget comparison; no account or order transport."""
from collections import Counter,defaultdict
import json,gzip,pickle,sys
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_noncrypto_tuning_2026_10_07'))
import study as s
ASSETS=('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI')
RULES={'COPPER':1239,'NATGAS':0,'EURUSD':1183,'WTI':1238}
def route(a):return 0 if a=='EURUSD' else 2

def simulate(rows,budget=10,exposure=19,stress=.01,cash0=50,cash2=50,max_contracts=20,z3_contracts=20,z3_exposure=20,z3_budget=25,correlation_cap=None):
    cash={0:cash0,2:cash2};active=[];closed=[];seen=set();blocks=Counter();daily=defaultdict(float);lane_daily=defaultdict(float)
    def settle(t):
        nonlocal active
        rest=[]
        for x in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
            if x['settled']<=t:
                cash[route(x['asset'])]+=5*x['won'];daily[s.r.etday(x['settled'])]+=x['net'];lane_daily[(s.r.etday(x['settled']),x['lane'])]+=x['net'];closed.append(x)
            else:rest.append(x)
        active=rest
    for x in sorted(rows,key=lambda r:(r['entered'],r['lane']!=s.r.Z2,ASSETS.index(r['asset']))):
        settle(x['entered'])
        if x['ticker'] in seen:continue
        risk=5*float(s.r.LANES[x['lane']].reserve);projected=risk+sum(y['reserve'] for y in active);day=s.r.etday(x['entered'])
        reason=''
        if any(y['asset']==x['asset'] for y in active):reason='asset'
        elif correlation_cap is not None and x['asset'] in ASSETS[:4] and sum(y['asset'] in ASSETS[:4] and y['side']==x['side'] and y['close']==x['close'] for y in active)>=correlation_cap:reason='crypto_same_side_expiry'
        elif 5*(len(active)+1)>max_contracts:reason='contracts'
        elif projected>exposure+1e-8:reason='exposure'
        elif daily[day]-projected < -budget-1e-8:reason='daily_including_open_risk'
        elif x['lane']==s.r.Z3:
            own=[y for y in active if y['lane']==s.r.Z3];ownrisk=risk+sum(y['reserve'] for y in own)
            if 5*(len(own)+1)>z3_contracts or ownrisk>z3_exposure+1e-8:reason='z3_own_exposure'
            elif lane_daily[(day,s.r.Z3)]-ownrisk < -z3_budget-1e-8:reason='z3_daily'
        if not reason and cash[route(x['asset'])]<max(5,risk)-1e-8:reason='route_cash'
        if reason:blocks[reason]+=1;continue
        net=x['pnl']-5*stress
        cash[route(x['asset'])]-=5*x['entry']+x['fee']+5*stress
        active.append(dict(x,net=net,reserve=risk));seen.add(x['ticker'])
    settle(float('inf'))
    return sorted(closed,key=lambda r:(r['settled'],r['ticker'])),dict(blocks)

def summary(rows):
    m=s.compact(rows);return {k:m[k] for k in ('trades','wins','losses','net','settled_drawdown')}

