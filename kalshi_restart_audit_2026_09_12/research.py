"""Frozen, causal minute-replay comparison. Research only; no network/orders."""
import argparse
import csv
from dataclasses import dataclass, asdict, replace
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics
import numpy as np

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'fresh_data'
ASSETS=('BTC','ETH','SOL','XRP')

@dataclass(frozen=True)
class Rule:
    name:str
    family:str
    kind:str
    lo:float=.85
    hi:float=.93
    remlo:int=3
    remhi:int=10
    spread:float=.02
    zmin:float=0
    source:str='none'
    exit:str='hold'
    maker:bool=False

def rules():
    out=[Rule('r1_original','unbroken','unbroken',.93,.99,4,8,.01),
         Rule('r1_deployed','unbroken','unbroken',.90,.99,4,8,.01),
         Rule('stabilized_flip','stabilized','stabilized',.93,.95,4,8,.01),
         Rule('late_favorite','late','plain',.98,.994,1,2,.01),
         Rule('breakout_85','breakout','breakout',.85,.93,7,8,.01),
         Rule('early_momentum','momentum','momentum',.55,.85,7,11,.02),
         Rule('early_reversal','reversal','reversal',.55,.85,7,11,.02),
         Rule('flipped_longshot','longshot','longshot',.02,.10,3,8,.02)]
    for src in ('coinbase','benchmark'):
        for z in (1.,1.5,2.):
            out.append(Rule(f'cushion_{src}_z{z:g}','cushion','cushion',zmin=z,source=src))
        out.extend([
            Rule(f'r1_cushion_{src}','unbroken_cushion','unbroken',.90,.99,4,8,.01,1.5,src),
            Rule(f'lock_{src}','lock','cushion',.90,.994,1,1,.02,1.96,src),
            Rule(f'model_value_{src}','model_value','model',.50,.93,3,10,.02,0,src)])
        out.append(Rule(f'calibrated_value_{src}','calibrated_value','learned',.55,.95,7,7,.02,-5,src))
    # Exit alternatives are fixed before outcome inspection, each same entry rule.
    for base in list(out):
        if base.name in ('r1_deployed','breakout_85','early_momentum','early_reversal',
                         'cushion_coinbase_z1.5','cushion_benchmark_z1.5'):
            for exit in ('tp5_sl8','tp10_sl8','stop40','time3'):
                out.append(replace(base,name=base.name+'_'+exit,exit=exit))
    # Explicit optimistic maker scenarios; never eligible for selection.
    for name in ('r1_original','cushion_benchmark_z1.5'):
        base=next(r for r in out if r.name==name)
        out.append(replace(base,name=name+'_maker_scenario',maker=True))
    return out

def ts(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime('%Y-%m-%d')
def split(d):return 'discovery' if d<'2026-08-28' else 'validation' if d<'2026-09-04' else 'test'
def fee(p):return math.ceil((.07*p*(1-p)-1e-12)*10000)/10000
def quote(c,field='c'):
    if not c:return None
    try:b,a=float(c['b'+field]),float(c['a'+field])
    except (TypeError,KeyError):return None
    return (b,a) if 0<=b<=a<=1 else None
def side_quote(q,side):return q if side==1 else (1-q[1],1-q[0])

def load_prices():
    prices={src:{a:{} for a in ASSETS} for src in ('coinbase','benchmark')}
    means={a:{} for a in ASSETS}
    for a in ASSETS:
        p=DATA/f'spot_{a}.jsonl'
        if p.exists():
            for line in p.read_text().splitlines():
                for r in json.loads(line)['rows']:prices['coinbase'][a][int(r[0])]=float(r[4])
    p=DATA/'benchmark.jsonl'
    if p.exists():
        for line in p.read_text().splitlines():
            r=json.loads(line)
            for t,close,mean in r['minutes']:
                prices['benchmark'][r['asset']][t]=close;means[r['asset']][t]=mean
    # Timestamp-indexed volatility; no joining nearest future observation.
    stats={src:{a:{} for a in ASSETS} for src in prices}
    for src in prices:
        for a,bars in prices[src].items():
            for t in sorted(bars):
                keys=range(t-60*60,t+1,60)
                if not all(k in bars for k in keys):continue
                vals=[bars[k] for k in keys]
                sigma=statistics.pstdev([math.log(y/x) for x,y in zip(vals,vals[1:])])
                if sigma>0:stats[src][a][t]=(bars[t],sigma)
    return prices,means,stats

def load_markets():
    all=[]
    for a in ASSETS:
        ms={m['ticker']:m for m in map(json.loads,(DATA/f'markets_{a}.jsonl').read_text().splitlines())}
        for row in map(json.loads,(DATA/f'candles_{a}.jsonl').read_text().splitlines()):
            m=ms[row['ticker']]; close=ts(m['close_time']);start=ts(m['open_time'])
            br={int((close-c['t'])//60):c for c in row['candles'] if start<c['t']<=close}
            all.append(dict(ticker=row['ticker'],asset=a,close=close,start=start,
                            result=1 if m['result']=='yes' else -1,br=br,
                            strike=m.get('floor_strike'),expiration=m.get('expiration_value')))
    return sorted(all,key=lambda m:(m['close'],m['asset']))

def features(m,prices,means,stats):
    out={};history=[]
    for rem in range(14,0,-1):
        q=quote(m['br'].get(rem))
        if q is None:history=[];continue
        side=1 if sum(q)>1 else -1;fb,fa=side_quote(q,side)
        history.append(side)
        held=0
        for s in reversed(history):
            if s!=side:break
            held+=1
        flips=sum(x!=y for x,y in zip(history,history[1:]))
        previous=quote(m['br'].get(rem+1));prev=side_quote(previous,side) if previous else None
        z={}
        t=m['close']-rem*60
        for src in prices:
            st=stats[src][m['asset']].get(t-60)
            strike=(means[m['asset']].get(m['start']-60) if src=='benchmark'
                    else prices[src][m['asset']].get(m['start']-60))
            if st and strike:
                z[src]=side*(st[0]/strike-1)/(st[1]*math.sqrt(rem))
        out[rem]=dict(side=side,bid=fb,ask=fa,mid=(fb+fa)/2,held=held,flips=flips,
                      full=len(history)==15-rem,prev=prev,z=z)
    return out

def qualifies(r,f,rem):
    if not r.remlo<=rem<=r.remhi:return False
    if f['ask']-f['bid']>r.spread+1e-8:return False
    px=f['mid'] if r.kind in ('unbroken','stabilized') else (1-f['bid'] if r.kind=='longshot' else f['ask'])
    if not r.lo-1e-8<=px<r.hi+(-1e-8 if r.hi in (.93,.95) else 1e-8):return False
    z=f['z'].get(r.source)
    if r.source!='none' and (z is None or z<r.zmin):return False
    if r.kind=='unbroken' and not (f['full'] and f['flips']==0 and f['held']>=5):return False
    if r.kind=='stabilized' and not (f['full'] and f['flips']>0 and f['held']>=5):return False
    if r.kind=='breakout' and not (f['prev'] and sum(f['prev'])/2<.85 and f['mid']-sum(f['prev'])/2>=.03):return False
    if r.kind=='momentum' and not (f['prev'] and f['mid']-sum(f['prev'])/2>=.03):return False
    if r.kind=='reversal' and not (f['prev'] and f['mid']-sum(f['prev'])/2<=-.03):return False
    if r.kind=='longshot' and f['flips']==0:return False
    if r.kind=='model' and (.5*(1+math.erf(z/math.sqrt(2)))-f['ask']-fee(f['ask'])<.03):return False
    if r.kind=='learned' and (f.get('prob',{}).get(r.source,0)-f['ask']-fee(f['ask'])<.03):return False
    return True

def trade(m,r,fs,delay=0):
    for rem in range(r.remhi,r.remlo-1,-1):
        f=fs.get(rem)
        if not f or not qualifies(r,f,rem):continue
        side=-f['side'] if r.kind=='longshot' else f['side']
        entry_rem=rem-1-delay
        nxt=m['br'].get(entry_rem);q=quote(nxt,'o')
        if not q or entry_rem<0:continue
        bid,entry=side_quote(q,side)
        signalask=(1-f['bid']) if r.kind=='longshot' else f['ask']
        # A capped IOC can refuse a run-away ask; no fill is credited above cap.
        if entry>min(.999,signalask+.01)+1e-8:continue
        if r.kind in ('cushion','model') and entry<r.lo-1e-8:continue
        paidfee=fee(entry)
        if r.maker:
            # Optimistic resting bid, fill only if next-minute ask traded below
            # the quote. An ask low is not proof of queue fill: scenario only.
            try:low=float(nxt['al']) if side==1 else 1-float(nxt['bh'])
            except (TypeError,KeyError):continue
            limit=f['bid']
            if low>=limit-1e-8:continue
            entry=limit;paidfee=0
        pnl=(side==m['result'])-entry-paidfee
        reason='settlement';exitprice=float(side==m['result'])
        if r.exit!='hold':
            for exrem in range(entry_rem,0,-1):
                cq=quote(m['br'].get(exrem))
                if cq is None:continue
                value=side_quote(cq,side)[0]
                trigger=(r.exit=='stop40' and value<=.40) or (r.exit=='time3' and entry_rem-exrem+1>=3)
                if r.exit in ('tp5_sl8','tp10_sl8'):
                    target=.05 if r.exit=='tp5_sl8' else .10
                    trigger=value>=entry+target or value<=entry-.08
                if trigger:
                    eq=quote(m['br'].get(exrem-1),'o')
                    if eq is None:continue
                    exitprice=side_quote(eq,side)[0]
                    pnl=exitprice-entry-paidfee-fee(exitprice);reason=r.exit;break
        return dict(rule=r.name,family=r.family,ticker=m['ticker'],asset=m['asset'],
                    day=day(m['close']),split=split(day(m['close'])),ts=m['close']-rem*60,
                    interval=m['close'],side=side,entry=entry,pnl=pnl,
                    won=int(side==m['result']),exit=reason,exitprice=exitprice,
                    z=f['z'].get(r.source),maker_scenario=r.maker,delay=delay,
                    signal_ask=signalask,entry_floor=r.lo if r.kind in ('cushion','model') else 0)
    return None

def metrics(rows,stress=.01,days=None):
    if not rows:return dict(n=0,ev_c=None,pnl=0,wr=None,maxdd=0,worst_day=0)
    pnl=np.array([x['pnl']-stress for x in rows])
    by_interval={}
    for x,p in zip(rows,pnl):by_interval[x['interval']]=by_interval.get(x['interval'],0)+float(p)
    cum=np.r_[0,np.cumsum([by_interval[t] for t in sorted(by_interval)])]
    daily={d:0. for d in (days or sorted({x['day'] for x in rows}))}
    for x,p in zip(rows,pnl):daily[x['day']]+=float(p)
    return dict(n=len(rows),ev_c=round(100*float(pnl.mean()),5),pnl=round(float(pnl.sum()),5),
                wr=round(sum(x['won'] for x in rows)/len(rows),5),
                maxdd=round(float(np.max(np.maximum.accumulate(cum)-cum)),5),
                worst_day=round(min(daily.values()),5))

def bootstrap(rows,days,alpha=.05):
    if not rows:return None
    sums=np.array([sum(x['pnl']-.01 for x in rows if x['day']==d) for d in days])
    counts=np.array([sum(x['day']==d for x in rows) for d in days])
    rng=np.random.default_rng(20260912)
    ix=rng.integers(0,len(days),(30000,len(days)))
    den=counts[ix].sum(axis=1);valid=den>0
    means=sums[ix].sum(axis=1)[valid]/den[valid]
    return dict(lower_c=round(100*float(np.quantile(means,alpha)),5),
                positive_probability=round(float(np.mean(means>0)),5))

def model_x(f,source):
    if source not in f['z'] or not 0<f['mid']<1:return None
    return [1.,math.log(f['mid']/(1-f['mid'])),max(-5,min(5,f['z'][source]))]

def fit_models(markets,features_by_market):
    models={}
    for source in ('coinbase','benchmark'):
        xx=[];yy=[]
        for m,fs in zip(markets,features_by_market):
            if day(m['close'])>='2026-08-28':continue
            f=fs.get(7)
            if not f or not .55<=f['ask']<=.99 or f['ask']-f['bid']>.02+1e-8:continue
            x=model_x(f,source)
            if x is None:continue
            xx.append(x);yy.append(int(f['side']==m['result']))
        if len(xx)<100:continue
        X=np.array(xx);y=np.array(yy);coef=np.array([0.,1.,0.])
        penalty=np.diag([0.,10.,10.])
        for _ in range(40):
            pred=1/(1+np.exp(-np.clip(X@coef,-30,30)))
            grad=X.T@(pred-y)+penalty@coef
            hessian=(X.T*(pred*(1-pred)))@X+penalty+np.eye(3)*1e-8
            step=np.linalg.solve(hessian,grad);coef-=step
            if np.max(np.abs(step))<1e-9:break
        models[source]=dict(n=len(xx),coef=coef.tolist(),train_end_exclusive='2026-08-28')
    return models

def main():
    p=argparse.ArgumentParser();p.add_argument('--freeze',action='store_true');args=p.parse_args()
    rr=rules()
    if args.freeze:
        (ROOT/'frozen_rules.json').write_text(json.dumps([asdict(r) for r in rr],indent=2))
        print('frozen',len(rr),'rules');return
    assert json.loads((ROOT/'frozen_rules.json').read_text())==[asdict(r) for r in rr], 'rules changed after freeze'
    prices,means,stats=load_prices();markets=load_markets()
    feature_rows=[features(m,prices,means,stats) for m in markets]
    models=fit_models(markets,feature_rows)
    (ROOT/'fitted_models.json').write_text(json.dumps(models,indent=2))
    allrows=[]; delayed=[];coverage={a:0 for a in ASSETS}; errors=[]
    for i,m in enumerate(markets):
        fs=feature_rows[i];coverage[m['asset']]+=1
        for f in fs.values():
            f['prob']={}
            for source,model in models.items():
                x=model_x(f,source)
                if x is not None:
                    value=sum(a*b for a,b in zip(x,model['coef']))
                    f['prob'][source]=1/(1+math.exp(-max(-30,min(30,value))))
        avg=means[m['asset']].get(m['start']-60)
        if avg and m['strike']:errors.append(abs(avg-float(m['strike'])))
        for r in rr:
            row=trade(m,r,fs)
            if row:allrows.append(row)
            if not r.maker:
                row=trade(m,r,fs,delay=1)
                if row:delayed.append(row)
        if i%2000==0:print('replayed',i,'/',len(markets),flush=True)
    # Derive shortlist without looking at final-period results.
    summary=[];selected={}
    for r in rr:
        rows=[x for x in allrows if x['rule']==r.name]
        a=metrics([x for x in rows if x['split']=='discovery']);b=metrics([x for x in rows if x['split']=='validation'])
        score=min(a['ev_c'] or -999,b['ev_c'] or -999)
        if not r.maker and a['n']>=100 and b['n']>=30 and score>0:
            if r.family not in selected or score>selected[r.family][0]:selected[r.family]=(score,r.name)
    shortlist=[v[1] for v in selected.values()]
    (ROOT/'shortlist.json').write_text(json.dumps(shortlist,indent=2))
    testdays=[f'2026-09-{d:02}' for d in range(4,12)]
    for r in rr:
        rows=[x for x in allrows if x['rule']==r.name]
        for sp in ('discovery','validation','test'):
            subset=[x for x in rows if x['split']==sp]
            m=metrics(subset)
            m.update(rule=r.name,family=r.family,split=sp,selected=r.name in shortlist,
                     ev_base_c=metrics(subset,0)['ev_c'],ev_half_c=metrics(subset,.005)['ev_c'])
            summary.append(m)
    with (ROOT/'comparison.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    for name,rs in [('trades',allrows),('delayed_trades',delayed)]:
        with (ROOT/(name+'.jsonl')).open('w') as f:
            for row in rs:f.write(json.dumps(row)+'\n')
    verdict=[]
    for name in shortlist:
        rows=[x for x in allrows if x['rule']==name and x['split']=='test']
        dr=[x for x in delayed if x['rule']==name and x['split']=='test']
        m=metrics(rows);ci=bootstrap(rows,testdays,alpha=.05/max(1,len(shortlist)))
        dm=metrics(dr)
        daily=[sum(x['pnl']-.01 for x in rows if x['day']==d) for d in testdays]
        concentrated=bool(m['pnl']>0 and max(daily)>.5*m['pnl'])
        passed=m['n']>=100 and m['ev_c']>0 and ci['lower_c']>0 and dm['n']>=100 and dm['ev_c']>0 and not concentrated
        verdict.append(dict(rule=name,test=m,delay=dm,adjusted_day_bootstrap=ci,passes_statistical_gate=passed,
                            largest_day_exceeds_half_profit=concentrated,daily_pnl=dict(zip(testdays,daily)),
                            by_asset={a:metrics([x for x in rows if x['asset']==a]) for a in ASSETS}))
    out=dict(markets=coverage,rules=len(rr),shortlist=verdict,
             benchmark_strike_median_error=statistics.median(errors) if errors else None,
             benchmark_strike_pairs=len(errors),
             input_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.glob('*') if p.is_file()})
    (ROOT/'research_results.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2),flush=True)

if __name__=='__main__':main()
