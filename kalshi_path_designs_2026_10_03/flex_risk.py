"""Isolated overlap risk model with preferred-five initial sizing fallback only."""
from replay import *

def flex_replay(ev,policy='baseline',stress=.01,capcost=False):
    seen={};active=[];accepted=[];daily=collections.defaultdict(float);own=collections.defaultdict(float)
    cash=50.;counts=collections.Counter();maxima=collections.defaultdict(float)
    for e in ev:
        still=[]
        for p in active:
            if p['settled']<=e['entered']:
                cash+=p['quantity']*p['won'];day=s.etday(p['settled']);daily[day]+=p['net']
                if p['lane']==1:own[day]+=p['net']
            else:still.append(p)
        active=still;prior=seen.get(e['ticker'],[]);second=bool(prior)
        if second:
            if policy!='parallel5' or len(prior)>=2 or any(p['lane']==e['lane'] for p in prior):continue
            if prior[0]['side']!=e['side'] or not any(p['ticker']==e['ticker'] for p in active):continue
        if any(p['asset']==e['asset'] and p['ticker']!=e['ticker'] for p in active):continue
        if policy!='parallel5' and any(p['asset']==e['asset'] for p in active):continue
        count=sum(p['quantity'] for p in active);risk=sum(p['reserve'] for p in active)
        owncount=sum(p['quantity'] for p in active if p['lane']==1);day=s.etday(e['entered'])
        target=int(policy[-2:] if policy.endswith('10') else policy[-1]) if policy.startswith(('overlap','strict')) and e['overlap'] else 5
        chosen=None
        for n in {'baseline':[5],'fallback2':[5,2],'fallback1':[5,1],'flex':[5,3,2,1]}[policy]:
            fill=e['fills'].get(n)
            if second and prior[0]['entered']==e['entered']:
                fill=e['second_fill']
            if fill is None:counts['insufficient_depth_'+str(n)]+=1;continue
            if n!=5 and policy.startswith('strict') and not e['strict'][n]:counts['strict_price_fallback']+=1;continue
            reserve=n*(.95 if e['profile']==0 else 1.)
            if count+n>20 or risk+reserve>19+1e-8:counts['capacity_block_'+str(n)]+=1;continue
            if daily[day]-risk-reserve < -10-1e-8:counts['daily_block_'+str(n)]+=1;continue
            if e['lane']==1 and (owncount+n>20 or own[day]-owncount-n < -25-1e-8):continue
            if cash<max(5.,reserve)-1e-8:counts['cash_block_'+str(n)]+=1;continue
            chosen=(n,fill,reserve);break
        if chosen is None:continue
        n,fill,reserve=chosen;price,paid=fill
        if capcost:price=e['cap'];paid=s.fee(price,n)
        debit=n*price+paid+stress*n;cash-=debit
        row={k:v for k,v in e.items() if k not in ('fills','strict','second_fill')}
        row.update(quantity=n,entry=price,fee=paid,reserve=reserve,pnl=n*(e['won']-price)-paid,pnl_cap=n*(e['won']-e['cap'])-s.fee(e['cap'],n),net=n*e['won']-debit,second=second,second_later=second and e['t']>prior[0]['t'])
        active.append(row);seen.setdefault(e['ticker'],[]).append(row);accepted.append(row)
        maxima['contracts']=max(maxima['contracts'],count+n);maxima['risk']=max(maxima['risk'],risk+reserve)
        maxima['daily_committed_loss']=max(maxima['daily_committed_loss'],risk+reserve-daily[day])
        assert count+n<=20 and risk+reserve<=19+1e-8 and risk+reserve-daily[day]<=10+1e-8
        assert cash>=-1e-8 and sum(p['quantity'] for p in seen[e['ticker']])<=10
    return accepted,dict(counts),dict(maxima)
