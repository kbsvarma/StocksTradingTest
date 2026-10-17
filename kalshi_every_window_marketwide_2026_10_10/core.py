"""Offline fixed-window execution/accounting. Contains no order interfaces."""
from common import *
from collections import Counter,defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo
import math
ET=ZoneInfo('America/New_York')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def fee(p,q,m=1):return math.ceil((.07*m*q*p*(1-p)-1e-12)*10000)/10000
def qte(c,side='yes',phase='close'):
    if not c:return None
    values=[]
    for field in ['yes_bid','yes_ask']:
        obj=c.get(field,{})
        if phase+'_dollars' in obj:v=obj[phase+'_dollars']
        else:
            v=obj.get(phase)
            if isinstance(v,int):v=v/100
        try:v=float(v)
        except (TypeError,ValueError):return None
        if not math.isfinite(v):return None
        values.append(v)
    b,a=values
    if not 0<=b<=a<=1:return None
    # Public dollar quotes have four decimal places. Normalize binary-float
    # complements before comparing legal cent/fractional-cent price boundaries.
    return (round(b,4),round(a,4)) if side=='yes' else (round(1-a,4),round(1-b,4))
def pay(m,side):
    if m.get('result') in ('yes','no'):return float(m['result']==side)
    v=m.get('settlement_value_dollars')
    return None if v is None else float(v) if side=='yes' else 1-float(v)
def simulate(rows,qty,friction,enforce=True):
    cash=100.;active=[];seen=set();real=defaultdict(float);out=[];blocked=Counter();peakreserve=0;mincash=100
    for r in sorted(rows,key=lambda x:(x['entered'],x['asset'],x['owner'],x['ticker'],x['side'])):
        if r['owner'] in seen:continue
        seen.add(r['owner'])
        for a in active:
            if a['settled']<=r['entered']:cash+=a['qty']*a['payout'];real[day(a['settled'])]+=a['net']
        active=[a for a in active if a['settled']>r['entered']]
        f=fee(r['entry'],qty,r['mult']);cost=qty*(r['entry']+friction)+f;reserve=sum(a['cost'] for a in active)+cost
        reason=''
        if enforce:
            if sum(a['qty'] for a in active)+qty>25:reason='contracts'
            elif reserve>25+1e-8:reason='exposure'
            elif real[day(r['entered'])]-reserve < -20-1e-8:reason='daily_risk'
            elif cash-cost<2-1e-8:reason='cash'
        if reason:blocked[reason]+=1;continue
        a={**r,'qty':qty,'fee':f,'cost':cost,'friction':friction,'net':qty*r['payout']-cost}
        cash-=cost;mincash=min(mincash,cash);peakreserve=max(peakreserve,reserve);active.append(a);out.append(a)
    return out,dict(blocked,min_cash=round(mincash,5),peak_reserved=round(peakreserve,5))
def stats(rows):
    eq=peak=dd=0;daily=defaultdict(float);counts=Counter()
    for r in sorted(rows,key=lambda x:(x['settled'],x['owner'])):
        eq+=r['net'];peak=max(peak,eq);dd=max(dd,peak-eq);daily[day(r['entered'])]+=r['net'];counts[day(r['entered'])]+=1
    return {'trades':len(rows),'wins':sum(r['payout']==1 for r in rows),'losses':sum(r['payout']==0 for r in rows),
            'scalar':sum(r['payout'] not in (0,1) for r in rows),'cash_wins':sum(r['net']>0 for r in rows),
            'net':round(eq,5),'drawdown':round(dd,5),'daily':{k:round(v,5) for k,v in sorted(daily.items())},
            'daily_trades':dict(sorted(counts.items())),'active_days':len(counts),'multi_trade_days':sum(v>=2 for v in counts.values()),
            'without_best3':round(eq-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),5)}
def record(result,ledgers,key,rows,denom,qty,friction):
    actual,blocked=simulate(rows,qty,friction);allrows,_=simulate(rows,qty,friction,False)
    result[key]={**stats(actual),'windows':denom,'execution_eligible':len(rows),'participation':len(actual)/denom if denom else 0,
                 'blocked':blocked,'unconstrained':stats(allrows)}
    ledgers[key]=actual
