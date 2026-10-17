"""Five predeclared FX mechanisms, causal training, no grid fitting or orders."""
import collections as C
import datetime as dt
import gzip
import json
import math
import pickle
import numpy as np
from pathlib import Path
import simulation as s
from pull import P,ASSETS,START,END,ts
from build import quote

CONFIG=[dict(id=0,name='strict_z2_z3',family='cushion',adaptive=False,rounded=False),
        dict(id=1,name='eurusd_adaptive',family='cushion',adaptive=True,rounded=False),
        dict(id=2,name='strict_rounded_strike',family='cushion',adaptive=False,rounded=True),
        dict(id=3,name='adaptive_rounded_strike',family='cushion',adaptive=True,rounded=True),
        dict(id=4,name='empirical_value_5m',family='value')]
CONFIG=CONFIG[:2]
PERIODS=dict(full=(START,END),prior=(START,ts('2026-10-12T04:00:00Z')),extension=(ts('2026-10-12T04:00:00Z'),END),early=(START,ts('2026-10-01T04:00:00Z')),late=(ts('2026-10-01T04:00:00Z'),END))

markets=pickle.loads((P/'features.pickle').read_bytes())
books={}

def empirical():
    pred={};audit=[]
    for a in ASSETS:
        rows=[(m,f) for m in markets if m['asset']==a for f in m['features'] if f['rem']==5]
        weeks=sorted({int((f['t']-345600)//604800)*604800+345600 for m,f in rows})
        for begin in weeks:
            train=[(m,f) for m,f in rows if m['settled']<begin]
            test=[(m,f) for m,f in rows if begin<=f['t']<begin+604800]
            if len(train)<250 or not test:continue
            rr=np.sort([f['residual'] for m,f in train])
            audit.append(dict(asset=a,week=begin,train=len(train),last_settled=max(m['settled'] for m,f in train),test=len(test)))
            for m,f in test:
                prob=(len(rr)-np.searchsorted(rr,f['threshold'],side='left')+1)/(len(rr)+2)
                pred[m['ticker'],f['t']]=float(.8*prob+.2*(f['ybid']+f['yask'])/2)
    assert all(r['last_settled']<r['week'] for r in audit)
    s.save('training_audit.json',audit);(P/'predictions.pickle').write_bytes(pickle.dumps(pred))
    return pred

def observed(ticker,T,side,floor,cap):
    samples=books.get((ticker,T,side))
    if samples is None:return 'unknown',None
    for b in samples:
        q=0;cost=0;below=False
        for px,n in sorted((1-float(px),float(n)) for px,n in b['levels']):
            if n<=0:continue
            if px<floor-1e-8:below=True
            if px>cap+1e-8:break
            take=min(5-q,n);q+=take;cost+=take*px
            if q>=5-1e-8:break
        if q>=5-1e-8 and not below:return 'supported',(cost/5,b['t'])
    return 'sampled_no_fill',None

def opportunities(c,pred,mode):
    out=[];counts={a:C.Counter() for a in ASSETS}
    for m in markets:
        for f in m['features']:
            options=[]
            if c['family']=='value':
                if f['rem']!=5:continue
                p=pred.get((m['ticker'],f['t']))
                if p is None:continue
                side,ask,pwin=max(((1,f['yask'],p),(-1,1-f['ybid'],1-p)),key=lambda x:x[2]-x[1]-s.fee(x[1],5,m['multiplier'])/5-.01)
                edge=pwin-ask-s.fee(ask,5,m['multiplier'])/5-.01
                if not .5-1e-8<=ask<.95-1e-8 or f['yask']-f['ybid']>.02000001 or edge<.02:continue
                options=[('value',side,ask,.5,.939,pwin)]
            else:
                if not (1 if c['adaptive'] else 3)<=f['rem']<=10 or f['ask']-f['bid']>.02000001:continue
                z=(-f['threshold']*f['side']) if c['rounded'] else f['z']
                for lane,base,floor,ceiling,absolute in (('original',1.75 if c['adaptive'] else 2,.85,.95,.939),('premium',2.75 if c['adaptive'] else 3,.93,.98,.975)):
                    threshold=max(.5,base+.5*math.log2(f['rem']/5)) if c['adaptive'] else base
                    if z>=threshold and floor-1e-8<=f['ask']<ceiling-1e-8:options.append((lane,f['side'],f['ask'],floor,absolute,None))
            for lane,side,ask,floor,absolute,pwin in options:
                counts[m['asset']]['qualified_minutes']+=1
                cap=s.cap(ask,side,absolute,json.dumps(m['ranges'],sort_keys=True))
                if ask>cap+1e-8:
                    counts[m['asset']]['signal_above_cap_worse_quote_reject']+=1
                    continue
                evidence,known=observed(m['ticker'],f['t'],side,floor,cap)
                counts[m['asset']][evidence]+=1
                pair=quote(m['candles'].get(f['t']+60),'c' if mode=='delay60' else 'o')
                entered=f['t']+(60 if mode=='delay60' else 3)
                if entered>=m['close']:
                    counts[m['asset']]['expired_before_entry']+=1
                    continue
                if mode=='all_cap':price=cap
                elif mode=='receipts' and evidence!='unknown':
                    if evidence=='sampled_no_fill':continue
                    px,entered=known;price=min(cap,max(ask,px))
                else:
                    if pair is None:counts[m['asset']]['missing_quote']+=1;continue
                    px=pair[1] if side==1 else 1-pair[0]
                    if not floor-1e-8<=px<=cap+1e-8:counts[m['asset']]['outside_cap_floor']+=1;continue
                    price=cap if mode=='cap' else min(cap,max(ask,px))
                paid=s.fee(price,5,m['multiplier']);won=int(m['result']==int(side==1))
                out.append(dict(ticker=m['ticker'],asset=m['asset'],config=c['id'],lane=lane,t=f['t'],entered=entered,
                    close=m['close'],settled=m['settled'],side=side,won=won,quantity=5,entry=price,cap=cap,ask=ask,
                    floor=floor,fee=paid,z=f['z'],rem=f['rem'],net_before_friction=5*(won-price)-paid,pwin=pwin,
                    reserve=5*(1 if lane in ('value','premium') else .95),evidence=evidence))
    return out,{a:dict(x) for a,x in counts.items()}

if __name__=='__main__':
    pred={};results={};ledgers={};checks=0
    for c in CONFIG:
        key=c['name'];results[key]={};ledgers[key]={}
        for label,mode,stress in (('quote1c','base',.01),('quote2c','base',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)):
            pool,counts=opportunities(c,pred,mode);results[key][label]={'counts':counts};ledgers[key][label]={}
            for a in ASSETS:
                results[key][label][a]={};ledgers[key][label][a]={}
                for period,(lo,hi) in PERIODS.items():
                    rows=[r for r in pool if r['asset']==a and lo<=r['close']<hi]
                    ledger,diag=s.portfolio(rows,stress);m=s.metrics(ledger)
                    assert abs(diag['final_cash']-100-m['net'])<1e-6
                    assert len(ledger)==len({r['ticker'] for r in ledger})
                    for r in ledger:
                        assert r['t']<=r['entered']<r['close']<=r['settled']
                        assert r['floor']-1e-8<=r['entry']<=r['cap']+1e-8
                        checks+=1
                    results[key][label][a][period]=dict(m,**diag,evidence=dict(C.Counter(r['evidence'] for r in ledger)))
                    ledgers[key][label][a][period]=ledger
        print(key,{a:{p:{k:results[key]['quote1c'][a][p][k] for k in ('trades','wins','losses','net','drawdown')} for p in ('full','extension')} for a in ASSETS},flush=True)
    s.save('results.json',results)
    with gzip.open(P/'ledgers.json.gz','wt') as f:json.dump(ledgers,f)
    s.save('validation.json',dict(passed=True,chronology_cap_unique_ownership_cash_rows=checks,
        scenario_notes='Same profiles; all-cap assumes fills; receipts veto sampled unavailable depth; unknown remains quote proxy, not a proven fill.',configurations=CONFIG))
