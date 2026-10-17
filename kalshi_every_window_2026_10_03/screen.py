"""Offline every-window research; no account, credential or order imports.

Raw market candles and completed underlying bars, deliberately no live-bot gates.
Freeze selection before evaluating later-period P&L. Keep full trial inventory.
"""
from pathlib import Path
import argparse, collections, datetime as dt, gzip, hashlib, json, math, pickle
import numpy as np

P=Path(__file__).resolve().parent
ROOT=P.parent
TIMES=[14,12,10,8,6,4,3,2,1]
POLICIES=['favorite','underdog','yes','no','spot_momentum','spot_reversal',
          'market_calibration','spot_probability','rich_probability']
EXITS=['settle','time1','time3','tp05_sl10','tp10_sl10','tp10','sl10']
SOURCE=ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle'
if (P/'input_sources/market_features_source.pickle').exists():SOURCE=P/'input_sources/market_features_source.pickle'
FOLDERS=[ROOT/'kalshi_restart_audit_2026_09_12/fresh_data',
         ROOT/'kalshi_parallel_design_2026_10_02/fresh_data',
         ROOT/'kalshi_execution_audit_2026_10_02/fresh_data',
         ROOT/'kalshi_btc_eth_quant_2026_10_02/fresh_data']
FOLDERS=[P/'input_sources'/str(i) if (P/'input_sources'/str(i)).exists() else folder for i,folder in enumerate(FOLDERS)]

def ts(s):return int(dt.datetime.fromisoformat(s+'T00:00:00+00:00').timestamp())
START=ts('2026-08-13');FIT=ts('2026-08-28');SELECT=ts('2026-09-12');TEST=ts('2026-10-02');END=ts('2026-10-03')
def cohort(t):return 'fit' if t<FIT else 'selection' if t<SELECT else 'test' if t<TEST else 'supplement'
def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat()
def save(name,x):(P/name).write_text(json.dumps(x,indent=2,allow_nan=False))
def fee(p,n=1,maker=False):return math.ceil((.0175 if maker else .07)*p*(1-p)*n*1e4-1e-9)/1e4
def quote(m,t,field='c'):
    c=m['candles'].get(t)
    if not c:return None
    try:b,a=float(c['b'+field]),float(c['a'+field])
    except (KeyError,TypeError,ValueError):return None
    return (b,a) if 0<=b<=a<=1 else None
def logit(p):
    p=min(.999,max(.001,p));return math.log(p/(1-p))
def phi(z):return .5*(1+math.erf(z/math.sqrt(2)))

def prepare():
    markets=[m for m in pickle.loads(SOURCE.read_bytes()) if m['asset'] in ('BTC','ETH') and START<=m['close']<END]
    bars={a:{} for a in ('BTC','ETH')};hashes={str(SOURCE.relative_to(ROOT)):hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
    for folder in FOLDERS:
        for a in bars:
            path=folder/('spot_'+a+'.jsonl')
            if not path.exists():continue
            hashes[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
            for line in path.open():
                for row in json.loads(line)['rows']:bars[a][int(row[0])]=float(row[4])
    ss={}
    for a,bb in bars.items():
        tt=np.arange(min(bb),max(bb)+60,60);v=np.array([bb.get(int(t),np.nan) for t in tt])
        ret=np.diff(np.log(v));ww=np.lib.stride_tricks.sliding_window_view(ret,60);sig=ww.std(axis=1)
        good=np.isfinite(ww).all(axis=1)&(sig>0)
        ss[a]={int(tt[i+60]+60):(float(v[i+60]),float(sig[i]),float(ww[i,-15:].std()/sig[i]),
            *[float(ww[i,-n:].sum()/(sig[i]*math.sqrt(n))) for n in (1,3,5,15,60)]) for i in np.flatnonzero(good)}
    count=collections.Counter()
    for m in markets:
        a=m['asset'];other='ETH' if a=='BTC' else 'BTC';m['new_features']={}
        for rem in range(14,0,-1):
            T=m['close']-rem*60;q=quote(m,T);s=ss[a].get(T);ref=bars[a].get(m['start']-60);o=ss[other].get(T)
            if q is None:count[a+'_missing_quote']+=1;continue
            f=dict(t=T,rem=rem,b=q[0],a=q[1],mid=sum(q)/2)
            lm=logit(f['mid']);lr=math.log(rem/7)
            f['xmarket']=[1,lm,lm*abs(lm),lm*lr]
            if s and ref and o:
                z=(s[0]/ref-1)/(s[1]*math.sqrt(rem));f.update(z=z,sigma=s[1],spot=s[0],ref=ref)
                pq=quote(m,T-60);dp=lm-logit(sum(pq)/2) if pq else 0
                ratio=math.log(max(.05,min(20,s[2])))
                f['xrich']=[1,lm,lm*abs(lm),lm*lr,max(-6,min(6,z)),z*ratio,
                    *[max(-6,min(6,x)) for x in s[3:]],max(-6,min(6,o[4])),dp]
            else:count[a+'_missing_model_bars']+=1
            m['new_features'][rem]=f
        m['cohort']=cohort(m['close'])
    save('data_quality.json',dict(source_hashes=hashes,counts=dict(count),markets_by_cohort=dict(collections.Counter(m['asset']+'_'+m['cohort'] for m in markets))))
    models={}
    for a in ('BTC','ETH'):
        for kind in ('market','rich'):
            xx=[];yy=[];ww=[]
            for m in markets:
                if m['asset']!=a or m['cohort']!='fit':continue
                fs=[m['new_features'][r] for r in TIMES if r in m['new_features'] and 'x'+kind in m['new_features'][r]]
                for f in fs:
                    x=np.array(f['x'+kind]);y=int(m['result']==1)
                    xx.extend([x,np.r_[x[0],-x[1:]]]);yy.extend([y,1-y]);ww.extend([.5/len(fs)]*2)
            X=np.array(xx);y=np.array(yy);w=np.array(ww)
            scale=np.sqrt(np.average(X*X,axis=0,weights=w));scale[:2]=1;scale[scale<1e-8]=1;X=X/scale
            coef=np.zeros(X.shape[1]);coef[1]=1;prior=coef.copy();penalty=np.eye(len(coef))*10
            for _ in range(80):
                pred=1/(1+np.exp(-np.clip(X@coef,-30,30)))
                grad=X.T@(w*(pred-y))+penalty@(coef-prior)
                h=(X.T*(w*pred*(1-pred)))@X+penalty
                step=np.linalg.solve(h,grad);coef-=step
                if abs(step).max()<1e-9:break
            models[a+'_'+kind]=dict(coef=coef.tolist(),scale=scale.tolist(),rows=len(y),market_weight=float(w.sum()))
            for m in markets:
                if m['asset']!=a:continue
                for f in m['new_features'].values():
                    if 'x'+kind in f:f['p'+kind]=float(1/(1+np.exp(-np.clip(np.array(f['x'+kind])/scale@coef,-30,30))))
    save('models.json',models);(P/'prepared.pickle').write_bytes(pickle.dumps(markets,protocol=5))
    return markets

def side(f,policy):
    fav=1 if f['mid']>.5 else -1
    if policy=='favorite':return fav
    if policy=='underdog':return -fav
    if policy=='yes':return 1
    if policy=='no':return -1
    if policy in ('spot_momentum','spot_reversal'):
        if 'z' not in f:return None
        return (1 if f['z']>0 else -1)*(1 if policy=='spot_momentum' else -1)
    p=f.get('pmarket') if policy=='market_calibration' else f.get('prich') if policy=='rich_probability' else phi(f['z']) if 'z' in f else None
    if p is None:return None
    ey=p-f['a']-fee(f['a']);en=1-p-(1-f['b'])-fee(1-f['b'])
    return 1 if ey>=en else -1

def execution(m,T,direction,buy=True,delay=False):
    t=T+60
    if delay and t>=m['close']:return None
    q=quote(m,t,'c' if delay else 'o')
    if q is None:return None
    price=(q[1] if buy else q[0]) if direction==1 else (1-q[0] if buy else 1-q[1])
    if not 0<price<1:return None
    return price, t if delay else T+3

def trade(m,f,direction,exit_rule='settle',delay=False,quantity=1):
    ent=execution(m,f['t'],direction,True,delay)
    if ent is None:return None
    entry,entered=ent;out=int(m['result']==direction);exit_ts=max(m['close']+1,m['settled']);exit_fee=0;reason='settlement'
    if exit_rule!='settle':
        for T in range(f['t']+60,m['close'],60):
            if T<=entered:continue
            q=quote(m,T)
            if q is None:continue
            bid=q[0] if direction==1 else 1-q[1]
            trigger=(exit_rule=='time1' and T>=entered+57) or (exit_rule=='time3' and T>=entered+177)
            trigger=trigger or ('tp05' in exit_rule and bid-entry>=.05-1e-9) or ('tp10' in exit_rule and bid-entry>=.10-1e-9)
            trigger=trigger or ('sl10' in exit_rule and bid-entry<=-.10+1e-9)
            if trigger:
                ex=execution(m,T,direction,False,delay)
                if ex:
                    out,exit_ts=ex;exit_fee=fee(out,quantity);reason=exit_rule;break
    paid=fee(entry,quantity)+exit_fee
    return dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],day=day(m['close']),cohort=m['cohort'],
        decision=f['t'],entered=entered,side=direction,entry=entry,exit=out,exit_ts=exit_ts,exit_reason=reason,
        quantity=quantity,fee=paid,pnl=quantity*(out-entry)-paid,settlement_win=int(m['result']==direction),legs=1+int(reason!='settlement'))

def metrics(rows,denominator,stress=.0025):
    rr=sorted(rows,key=lambda r:(r['exit_ts'],r['ticker']))
    pp=np.array([r['pnl']-stress*r['quantity']*r['legs'] for r in rr]);eq=np.r_[0,np.cumsum(pp)]
    daily=collections.defaultdict(float)
    for r,p in zip(rr,pp):daily[r['day']]+=float(p)
    n=len(rr)
    return dict(n=n,denominator=denominator,coverage=n/denominator if denominator else 0,
        positive_trades=int(sum(pp>0)),negative_trades=int(sum(pp<0)),settlement_wins=sum(r['settlement_win'] for r in rr),
        net=round(float(pp.sum()),6),raw_net=round(sum(r['pnl'] for r in rr),6),ev_c=round(float(pp.mean()*100),6) if n else 0,
        drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),worst_day=round(min(daily.values()),6) if daily else 0,
        mean_entry=float(np.mean([r['entry'] for r in rr])) if n else None,early_exits=sum(r['exit_reason']!='settlement' for r in rr),daily=dict(daily))

def config_key(a,p,r,e):return f'{a}/{p}/rem{r}/{e}'
def screen(markets,cohorts):
    results={};selected_rows={}
    for a in ('BTC','ETH'):
        for policy in POLICIES:
            for rem in TIMES:
                for exit_rule in EXITS:
                    key=config_key(a,policy,rem,exit_rule);rows=[];den=collections.Counter();skips=collections.Counter()
                    for m in markets:
                        if m['asset']!=a or m['cohort'] not in cohorts:continue
                        den[m['cohort']]+=1;f=m['new_features'].get(rem)
                        if f is None:skips['missing_signal_quote']+=1;continue
                        s=side(f,policy)
                        if s is None:skips['missing_model_inputs']+=1;continue
                        tr=trade(m,f,s,exit_rule)
                        if tr is None:skips['non_executable_next_quote']+=1;continue
                        rows.append(tr)
                    results[key]={co:metrics([r for r in rows if r['cohort']==co],den[co]) for co in cohorts}
                    results[key]['skips']=dict(skips)
    return results

def choose(result):
    passing=[k for k,v in result.items() if all(v[c]['coverage']>=.90 and v[c]['net']>0 for c in ('fit','selection'))]
    ranked=sorted(passing,key=lambda k:result[k]['selection']['net'],reverse=True)
    fallback=sorted(result,key=lambda k:result[k]['selection']['net'],reverse=True)
    chosen=[]
    # At most one each: BTC settlement, BTC early exit, ETH control.
    for asset,hold in [('BTC',True),('BTC',False),('ETH',None)]:
        subset=[k for k in ranked if k.startswith(asset+'/') and (hold is None or (k.endswith('/settle')==hold))]
        chosen.append(dict(group=asset+('_hold' if hold else '_exit' if hold is not None else ''),key=subset[0] if subset else None))
    return dict(passing=len(passing),selected=chosen,top_passing=ranked[:20],top_selection_diagnostics=fallback[:12],total_trials=len(result),
        selection_rule='positive fit AND selection after fees +0.25c per executed leg, >=90% coverage both; highest selection net within each group')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','select','test']);args=ap.parse_args()
    markets=prepare() if args.mode=='prepare' else pickle.loads((P/'prepared.pickle').read_bytes())
    if args.mode=='prepare':print('Prepared',len(markets));return
    if args.mode=='select':
        result=screen(markets,['fit','selection']);save('selection_screen.json',result)
        selected=choose(result);save('selection_frozen.json',selected);print(json.dumps(selected,indent=2))
    else:
        frozen=json.loads((P/'selection_frozen.json').read_text());out={};ledgers={}
        for ch in frozen['selected']:
            if not ch['key']:continue
            a,policy,rr,e=ch['key'].split('/');rem=int(rr[3:]);ledgers[ch['key']]={};out[ch['key']]={}
            for delay in (False,True):
                rows=[]
                for m in markets:
                    if m['asset']!=a:continue
                    f=m['new_features'].get(rem)
                    if f is None:continue
                    s=side(f,policy)
                    if s is None:continue
                    tr=trade(m,f,s,e,delay)
                    if tr:rows.append(tr)
                name='delay60' if delay else 'next_open';ledgers[ch['key']][name]=rows
                out[ch['key']][name]={co:{str(st):metrics([r for r in rows if r['cohort']==co],sum(m['asset']==a and m['cohort']==co for m in markets),st) for st in (0,.0025,.005,.01)} for co in ('fit','selection','test','supplement')}
        # Full fixed-time settlement family retained, not used to change frozen choice.
        global EXITS
        EXITS=['settle'];save('test_hold_inventory.json',screen(markets,['test','supplement']))
        save('selected_test.json',out)
        with gzip.open(P/'selected_trades.json.gz','wt') as h:json.dump(ledgers,h)
        print(json.dumps({k:{ex:{c:v['0.0025'] for c,v in co.items()} for ex,co in vv.items()} for k,vv in out.items()},indent=2))

if __name__=='__main__':main()
