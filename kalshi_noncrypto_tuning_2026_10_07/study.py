"""Isolated full-input parameter research. No network or account transport."""
from collections import Counter
from functools import lru_cache
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import pickle
import sys
import numpy as np

P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_noncrypto15m_2026_10_07'
sys.path.insert(0,str(OLD))
import replay as r
r.etday=lru_cache(maxsize=None)(r.etday)
ASSETS=('NATGAS','COPPER','WTI','EURUSD','GOLD')
WINDOWS=((1,3),(1,5),(2,5),(3,5),(3,7),(3,10),(5,10),(7,12),(10,14),(1,10),(1,14))
ZLOW=(.75,1,1.25,1.5,1.75,2,2.25,2.5,2.75,3,3.5,4)
ZHIGH=(1.5,2,2.5,2.75,3,3.5,4,4.5,5)

def save(name,obj):
    (P/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def configs():
    out=[]
    def add(family,z2,z3,lo,hi,slope=0,floor2=.85,floor3=.93):
        c=dict(id=len(out),family=family,z2=z2,z3=z3,lo=lo,hi=hi,slope=slope,floor2=floor2,floor3=floor3)
        out.append(c)
    add('baseline',2,3,3,10)
    for lo,hi in WINDOWS:
        for a in ZLOW:
            for b in ZHIGH:
                if b>=a and (a,b,lo,hi)!=(2,3,3,10):add('joint',a,b,lo,hi)
            add('standard_only',a,None,lo,hi)
        for b in ZHIGH:add('premium_only',None,b,lo,hi)
    pairs=((1.5,2.5),(1.75,2.75),(2,3),(2.25,3.25),(2.5,3.5))
    broad=((1,5),(2,5),(3,10),(1,10),(1,14))
    for a,b in pairs:
        for slope in (-.25,.25,.5):
            for lo,hi in broad:add('adaptive',a,b,lo,hi,slope=slope)
    for a,b in ((1.5,2.5),(1.75,2.75),(2,3),(2.5,3.5)):
        for lo,hi in broad:
            for floor2,floor3 in ((.80,.93),(.90,.93),(.85,.95)):
                add('price_floor',a,b,lo,hi,floor2=floor2,floor3=floor3)
    return out

CONFIG=configs()

def execution_cap(signal):
    # Same tick arithmetic as production; broad precomputation permits the
    # explicitly researched 80c floor. Each selected rule rechecks its own floor.
    cap=min(D(str(signal['signal_ask']))+D('.01'),r.LANES[signal['lane']].cap)
    yes=cap if signal['side']=='yes' else 1-cap
    for tick in signal['price_ranges']:
        lo,hi,step=map(D,(tick['start'],tick['end'],tick['step']))
        if step<=0:raise ValueError('invalid tick')
        if lo<=yes<=hi:
            direction=ROUND_FLOOR if signal['side']=='yes' else ROUND_CEILING
            px=lo+((yes-lo)/step).to_integral_value(rounding=direction)*step
            if lo<=px<=hi:return float(px if signal['side']=='yes' else 1-px)
    raise ValueError('no valid tick')

def partition(asset,name):
    start=r.ts('2026-09-23T00:00:00Z') if asset=='EURUSD' else r.START
    cut=r.ts('2026-09-28T00:00:00Z') if asset=='EURUSD' else r.ts('2026-09-25T00:00:00Z')
    return {'development':(start,cut),'selection':(cut,r.SPLIT),
            'confirmation':(r.SPLIT,r.END),'pooled':(r.START,r.END)}[name]

def rebuild():
    old=pickle.loads((OLD/'features.pickle').read_bytes())
    bars=pickle.loads((OLD/'underlying_minutes.pickle').read_bytes())
    result=[]; counts=Counter(); maxdiff=0
    for m in old:
        if m['asset'] not in ASSETS:continue
        prev={x['t']:x for x in m['features']}
        copy=dict(m,features=[])
        for rem in range(14,0,-1):
            T=m['close']-rem*60
            pair=r.quote(m['candles'].get(T))
            if pair is None:counts[m['asset']+'_missing_quote']+=1;continue
            side=1 if sum(pair)>1 else -1
            bid,ask=pair if side==1 else (1-pair[1],1-pair[0])
            extra=r.feature(bars[m['asset']],m['start'],T,rem,side)
            if extra is None:counts[m['asset']+'_incomplete_history']+=1;continue
            f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask,**extra)
            if T in prev:
                delta=max(abs(prev[T][k]-f[k]) for k in f)
                assert delta<1e-10,(m['ticker'],T,delta)
                maxdiff=max(maxdiff,delta);counts['existing_features_matched']+=1
            copy['features'].append(f);counts[m['asset']+'_features']+=1
        result.append(copy);counts[m['asset']+'_markets']+=1
    (P/'features.pickle').write_bytes(pickle.dumps(result))
    save('feature_validation.json',dict(counts=dict(counts),max_existing_difference=maxdiff,
         input_sha256={x:digest(OLD/x) for x in ('features.pickle','underlying_minutes.pickle','results.json')},
         complete_minute_only=True,range_remaining_minutes=[1,14]))
    save('configurations.json',CONFIG)
    save('protocol_freeze.json',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),
         spec_sha256=digest(P/'SPEC.txt'),source_sha256=digest(P/'study.py'),configurations=len(CONFIG),
         outcome_selection_stage='No tuned results calculated yet',live_changes=False))
    print('REBUILD',dict(counts),'CONFIGURATIONS',len(CONFIG),flush=True)

def pool(markets,mode='opening',precision='.0001'):
    rows=[]
    for m in markets:
        for f in m['features']:
            if f['ask']-f['bid']>.02000001:continue
            for lane,floor,ceiling in ((r.Z2,.80,.95),(r.Z3,.93,.98)):
                if not floor-1e-8<=f['ask']<ceiling-1e-8:continue
                sig=dict(lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges'])
                cap=execution_cap(sig)
                pair=r.quote(m['candles'].get(f['t']+60),'c' if mode=='delay60' else 'o',f['side'])
                if pair is None or not floor-1e-8<=pair[1]<=cap+1e-8:continue
                entry=cap if mode=='cap_cost' else pair[1]
                fee=r.fees(entry,5,precision,m.get('multiplier',1))
                won=int(f['side']==m['result'])
                rows.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=f['t'],
                     entered=f['t']+(60 if mode=='delay60' else 3),close=m['close'],settled=m['settled'],
                     side=f['side'],entry=entry,observed_entry=pair[1],cap=cap,quantity=5,fee=fee,won=won,
                     pnl=5*(won-entry)-fee,ask=f['ask'],bid=f['bid'],rem=f['rem'],z=f['z'],mode=mode))
    return rows

def select(rows,c):
    out=[]
    for x in rows:
        if not c['lo']<=x['rem']<=c['hi']:continue
        low=x['lane']==r.Z2; z=c['z2'] if low else c['z3']
        floor=c['floor2'] if low else c['floor3']
        if z is None or min(x['ask'],x['observed_entry'])<floor-1e-8:continue
        threshold=max(.5,z+c['slope']*math.log2(x['rem']/5))
        if x['z']+1e-12>=threshold:out.append(x)
    return out

def compact(rows):
    return {k:v for k,v in r.metrics(rows).items() if k!='daily'}

def evaluate(rows,c,asset,period,stress=.01,ledger=False):
    start,end=partition(asset,period)
    selected,blocked=r.portfolio(select([x for x in rows if x['asset']==asset and start<=x['close']<end],c),stress)
    metric=compact(selected);metric['blocked']=blocked
    return (metric,selected) if ledger else metric

def baseline_check(markets):
    expected=json.loads((OLD/'results.json').read_text())['opening_plus1c']['periods']['pooled']
    rows=pool(markets); controls={}
    for a in ASSETS:
        got=evaluate(rows,CONFIG[0],a,'pooled')
        for key in ('trades','wins','losses','net','settled_drawdown'):
            assert abs(got[key]-expected[a][key])<1e-7,(a,key,got,expected[a])
        controls[a]=got
    save('baseline_control.json',controls)
    print('All five unchanged full-design controls exactly reproduced',flush=True)

def distance(c):
    return sum(abs((c[k] if c[k] is not None else 6)-v) for k,v in (('z2',2),('z3',3),('lo',3),('hi',10)))+10*abs(c['slope'])+100*abs(c['floor2']-.85)+100*abs(c['floor3']-.93)

def train(markets):
    rows=pool(markets); result={}; shortlist={}
    for a in ASSETS:
        lo,hi=partition(a,'development')
        rr=[x for x in rows if x['asset']==a and lo<=x['close']<hi]
        table=[]
        for c in CONFIG:
            picked=select(rr,c)
            base,_=r.portfolio(picked,.01);cost,_=r.portfolio(picked,.02)
            b,k=compact(base),compact(cost)
            table.append(dict(id=c['id'],base=b,cost2=k,score=k['net']-.5*k['settled_drawdown']))
        valid=[x for x in table if x['base']['trades']>=15]
        ranked=sorted(valid,key=lambda x:(-x['score'],distance(CONFIG[x['id']]),x['id']))
        ids={0}|{x['id'] for x in ranked[:12]}
        for family in sorted({c['family'] for c in CONFIG}):
            ids.update(x['id'] for x in [x for x in ranked if CONFIG[x['id']]['family']==family][:4])
        # Predetermined one-threshold perturbations retain the other baseline lane.
        ids.update(c['id'] for c in CONFIG if c['lo']==3 and c['hi']==10 and c['slope']==0
                   and c['floor2']==.85 and c['floor3']==.93 and
                   ((c['z3']==3 and c['z2'] in ZLOW) or (c['z2']==2 and c['z3'] in ZHIGH)))
        result[a]=table;shortlist[a]=sorted(ids)
        print('TRAIN',a,'top',[(x['id'],x['base']['trades'],x['base']['net'],x['score']) for x in ranked[:3]],'shortlist',len(ids),flush=True)
    with gzip.open(P/'development_screen.json.gz','wt') as f:json.dump(result,f)
    save('shortlist_frozen.json',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),
          selection_dates_not_evaluated=True,confirmation_not_evaluated=True,ids=shortlist))

def choose(markets):
    frozen=json.loads((P/'shortlist_frozen.json').read_text())['ids']
    pools={'opening':pool(markets),'cap':pool(markets,'cap_cost')}
    choices={};detail={}
    for a in ASSETS:
        table=[]
        for i in frozen[a]:
            c=CONFIG[i];item={'id':i}
            for period in ('development','selection'):
                item[period]={'base':evaluate(pools['opening'],c,a,period),
                              'cost2':evaluate(pools['opening'],c,a,period,.02),
                              'cap':evaluate(pools['cap'],c,a,period)}
            d,v=item['development'],item['selection']
            item['pass']=d['base']['trades']>=15 and v['base']['trades']>=5 and all(item[p][s]['net']>0 for p in ('development','selection') for s in ('cost2','cap'))
            rates=[item[p][s]['net']/((partition(a,p)[1]-partition(a,p)[0])/86400) for p in ('development','selection') for s in ('cost2','cap')]
            days=max((partition(a,p)[1]-partition(a,p)[0])/86400 for p in ('development','selection'))
            item['score']=min(rates)-.1*max(item[p][s]['settled_drawdown'] for p in ('development','selection') for s in ('cost2','cap'))/days
            table.append(item)
        survivors=[x for x in table if x['pass']]
        survivors.sort(key=lambda x:(-x['score'],-sum(x[p]['cost2']['net'] for p in ('development','selection')),distance(CONFIG[x['id']]),x['id']))
        netleader=max(table,key=lambda x:sum(x[p]['base']['net'] for p in ('development','selection')))
        frequency=max(survivors,key=lambda x:sum(x[p]['base']['trades'] for p in ('development','selection'))) if survivors else None
        choices[a]=dict(robust=survivors[0]['id'] if survivors else 0,robust_pass=bool(survivors),
                        net_leader=netleader['id'],frequency_leader=frequency['id'] if frequency else 0,
                        pass_count=len(survivors),ranking=[x['id'] for x in survivors])
        detail[a]=table
        print('FROZEN',a,choices[a],CONFIG[choices[a]['robust']],flush=True)
    save('selection_results.json',detail)
    save('choices_frozen.json',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),
         confirmation_not_evaluated=True,choices=choices,source_sha256=digest(P/'study.py')))

SCENARIOS=(('opening1','opening',.01,'.0001'),('opening2','opening',.02,'.0001'),
           ('cap1','cap_cost',.01,'.0001'),('cent1','opening',.01,'.01'),
           ('delay60','delay60',.01,'.0001'))

def confirmation(markets):
    choices=json.loads((P/'choices_frozen.json').read_text())['choices']
    ids={a:sorted({0,*[choices[a][k] for k in ('robust','net_leader','frequency_leader')]}) for a in ASSETS}
    output={a:{} for a in ASSETS};ledgers={}
    for scenario,mode,stress,precision in SCENARIOS:
        rows=pool(markets,mode,precision)
        for a in ASSETS:
            for i in ids[a]:
                entry=output[a].setdefault(str(i),dict(config=CONFIG[i],scenarios={}))
                entry['scenarios'][scenario]={}
                for period in ('development','selection','confirmation','pooled'):
                    metric,trades=evaluate(rows,CONFIG[i],a,period,stress,True)
                    entry['scenarios'][scenario][period]=metric
                    ledgers[f'{a}/{i}/{scenario}/{period}']=trades
    save('finalist_results.json',output)
    with gzip.open(P/'finalist_ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
    for a in ASSETS:
        for i in ids[a]:
            x=output[a][str(i)]['scenarios']['opening1']
            print('RESULT',a,i,[(p,x[p]['trades'],x[p]['wins'],x[p]['losses'],x[p]['net'],x[p]['settled_drawdown']) for p in x],flush=True)

def diagnostics(markets):
    choices=json.loads((P/'choices_frozen.json').read_text())['choices']
    rows=pool(markets);neighbors={};zonly={};missed={}
    with gzip.open(P/'finalist_ledgers.json.gz','rt') as f:ledgers=json.load(f)
    for a in ASSETS:
        c=CONFIG[choices[a]['robust']]; ns=[]
        for key,step in (('z2',.25),('z3',.25),('lo',1),('hi',1)):
            if c[key] is None:continue
            for delta in (-step,step):
                n=dict(c);n[key]+=delta
                if n['lo']<1 or n['hi']>14 or n['lo']>n['hi'] or (key.startswith('z') and n[key]<.5):continue
                ns.append(dict(change=key,delta=delta,config=n,
                      results={p:evaluate(rows,n,a,p) for p in ('development','selection','confirmation','pooled')},
                      cost2=evaluate(rows,n,a,'pooled',.02)))
        neighbors[a]=ns
        zonly[a]=[]
        for z in ZLOW:
            c0=dict(CONFIG[0],z2=z)
            zonly[a].append(dict(z2=z,z3=3,**{p:evaluate(rows,c0,a,p) for p in ('development','selection','confirmation','pooled')}))
        # Independent per-market unavailable IOC proxy, fixed seeds; later
        # candidates remain eligible, preserving chronology and risk capacity.
        sims=[]; start,end=partition(a,'pooled')
        aa=[x for x in rows if x['asset']==a and start<=x['close']<end]
        tickers=sorted({x['ticker'] for x in aa}); rr=select(aa,c);bb=select(aa,CONFIG[0])
        for seed in range(100):
            rng=np.random.default_rng(71000+seed)
            absent={x for x,v in zip(tickers,rng.random(len(tickers))) if v<.25}
            picked,_=r.portfolio([x for x in rr if x['ticker'] not in absent]);base,_=r.portfolio([x for x in bb if x['ticker'] not in absent])
            sims.append(dict(net=compact(picked)['net'],delta=compact(picked)['net']-compact(base)['net'],trades=len(picked)))
        trades=ledgers[f'{a}/{c["id"]}/opening1/pooled'];base=ledgers[f'{a}/0/opening1/pooled']
        days=sorted({r.etday(x['settled']) for x in trades+base})
        pd=r.metrics(trades)['daily'];bd=r.metrics(base)['daily']
        daily=np.array([pd.get(d,0)-bd.get(d,0) for d in days]);rng=np.random.default_rng(20261007)
        boot=rng.choice(daily,size=(10000,len(days)),replace=True).sum(axis=1) if len(days) else np.zeros(10000)
        missed[a]=dict(market_failure_probability=.25,simulations=sims,
            delta_5_50_95=list(np.quantile([x['delta'] for x in sims],[.05,.5,.95])),
            positive_delta_seeds=sum(x['delta']>0 for x in sims),
            descriptive_unadjusted_day_bootstrap_delta_95=list(np.quantile(boot,[.025,.975])),
            one_additional_winner_flipped_loss_net=round(sum(x['net'] for x in trades)-5,5),
            warning='Selected/reused history; intervals do not correct multiple testing or unseen tails.')
    save('neighbors.json',neighbors);save('z_only_surface.json',zonly);save('robustness.json',missed)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['build','train','select','confirm','diagnostics']);args=parser.parse_args()
    if args.stage=='build':rebuild();return
    markets=pickle.loads((P/'features.pickle').read_bytes())
    if args.stage=='train':baseline_check(markets);train(markets)
    elif args.stage=='select':choose(markets)
    elif args.stage=='confirm':confirmation(markets)
    else:diagnostics(markets)

if __name__=='__main__':main()
