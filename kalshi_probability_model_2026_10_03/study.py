"""Reproducible official-index probability experiment, isolated from production."""
from pathlib import Path
import collections,datetime as dt,gzip,hashlib,json,math,sys,time
import numpy as np
from model import *
P=Path(__file__).resolve().parent;R=P.parent;INPUT=P/'inputs'
ASSETS=('BTC','ETH');KINDS=('market','gaussian','empirical','blended')
DATES=('2026-08-13','2026-08-28','2026-09-12','2026-10-02','2026-10-03','2026-10-04')
BOUNDS=[int(dt.datetime.fromisoformat(s+'T00:00:00+00:00').timestamp()) for s in DATES]
PERIODS=('distribution_fit','blend_fit','later','october2','october3')
def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def timestamp(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def period(t):return int(np.searchsorted(BOUNDS[1:],t,side='right'))
def inputs():
    if (INPUT/'markets_raw.jsonl.gz').exists():return
    folders=[R/f'kalshi_z_time_research_2026_10_03/input_sources/{i}' for i in range(4)]+[R/'kalshi_path_designs_2026_10_03/oct3_public']
    meta={};candles={};sources=[]
    for folder in folders:
        for asset in ASSETS:
            for kind in ('markets','candles'):
                path=folder/f'{kind}_{asset}.jsonl';sources.append(dict(path=str(path),sha256=sha(path)))
                for line in path.open():
                    row=json.loads(line)
                    if kind=='markets':meta[row['ticker']]=row
                    else:candles[row['ticker']]=row['candles']
    for kind,rows in [('markets_raw',list(meta.values())),('candles_raw',[dict(ticker=k,candles=v) for k,v in candles.items()])]:
        with gzip.open(INPUT/(kind+'.jsonl.gz'),'wt') as f:
            for r in rows:f.write(json.dumps(r,separators=(',',':'))+'\n')
    benchmark=[]
    for path in [R/'kalshi_restart_audit_2026_09_12/fresh_data/benchmark.jsonl',R/'kalshi_btc_eth_quant_2026_10_02/fresh_data/benchmark.jsonl']:
        sources.append(dict(path=str(path),sha256=sha(path)))
        benchmark.extend(r for r in map(json.loads,path.read_text().splitlines()) if r['asset'] in ASSETS)
    with gzip.open(INPUT/'index_archive.jsonl.gz','wt') as f:
        for r in benchmark:f.write(json.dumps(r,separators=(',',':'))+'\n')
    save('input_sources.json',sources)
def build():
    inputs();meta={r['ticker']:r for r in map(json.loads,gzip.open(INPUT/'markets_raw.jsonl.gz','rt'))}
    candles={r['ticker']:{int(c['t']):c for c in r['candles']} for r in map(json.loads,gzip.open(INPUT/'candles_raw.jsonl.gz','rt'))}
    cf={a:{} for a in ASSETS};av={a:{} for a in ASSETS}
    files=[INPUT/'index_archive.jsonl.gz',INPUT/'index_supplement.jsonl']
    for path in files:
        if not path.exists():continue
        with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as f:
            for r in map(json.loads,f):
                for t,last,mean in r['minutes']:cf[r['asset']][int(t)]=float(last);av[r['asset']][int(t)]=float(mean)
    stats={}
    for asset in ASSETS:
        bb=cf[asset];keys=np.arange(min(bb),max(bb)+60,60);v=np.array([bb.get(int(t),np.nan) for t in keys]);ret=np.diff(np.log(v));win=np.lib.stride_tricks.sliding_window_view(ret,60)
        sig=np.std(win,axis=1);good=np.isfinite(win).all(axis=1)&(sig>0)
        stats[asset]={int(keys[i+60]+60):(float(v[i+60]),float(sig[i])) for i in np.flatnonzero(good)}
    mm=[];rows=[];coverage=collections.Counter();checks=collections.defaultdict(list)
    for m in sorted(meta.values(),key=lambda m:(m['close_time'],m['ticker'])):
        a=m['ticker'].split('-')[0][2:-3];close=timestamp(m['close_time']);start=timestamp(m['open_time'])
        if not BOUNDS[0]<=close<BOUNDS[-1] or a not in ASSETS or m.get('result') not in ('yes','no'):continue
        if not m.get('floor_strike') or not m.get('expiration_value'):coverage['missing_strike_or_label']+=1;continue
        K=float(m['floor_strike']);endprice=float(m['expiration_value'].replace(',',''));y=int(m['result']=='yes');mi=len(mm);co=period(close)
        assert (endprice>=K)==bool(y),(m['ticker'],endprice,K,y)
        if start-60 in av[a]:checks[a+'_strike_error'].append(abs(round(av[a][start-60],2)-K))
        if close-60 in av[a]:checks[a+'_expiration_error'].append(abs(round(av[a][close-60],2)-endprice))
        mm.append(dict(ticker=m['ticker'],asset=a,start=start,close=close,settled=timestamp(m.get('settlement_ts') or m['close_time']),result=y,strike=K,expiration=endprice,cohort=PERIODS[co]))
        coverage[f'{a}_{PERIODS[co]}_markets']+=1
        for rem in range(14,0,-1):
            t=close-rem*60;price_sig=stats[a].get(t);c=candles.get(m['ticker'],{});q=c.get(t)
            if not price_sig:coverage[f'{a}_{PERIODS[co]}_missing_index_rows']+=1;continue
            if not q:coverage['missing_signal_candle']+=1;continue
            def quote(tt,mode):
                r=c.get(tt,{})
                try:b=float(r['b'+mode]);ask=float(r['a'+mode])
                except (KeyError,TypeError,ValueError):return np.nan,np.nan
                if not 0<=b<=ask<=1:return np.nan,np.nan
                return b,ask
            b,ask=quote(t,'c');eb,ea=quote(t+60,'o');db,da=quote(t+60,'c')
            if not np.isfinite(b+ask):coverage['invalid_signal_quote']+=1;continue
            S,sigma=price_sig;scale=float(horizon(sigma,rem));threshold=(K/S-1)/scale;residual=(endprice/S-1)/scale
            rows.append([mi,t,rem,S,sigma,threshold,residual,b,ask,eb,ea,db,da,y,co,ASSETS.index(a)])
    z=np.array(rows);np.save(INPUT/'rows.npy',z);(INPUT/'markets.json').write_text(json.dumps(mm,separators=(',',':')))
    out=dict(counts=dict(coverage),rows=len(z),markets=len(mm),official_index_minutes={a:len(cf[a]) for a in ASSETS},checks={k:dict(n=len(v),max=max(v),median=float(np.median(v)),over_one_cent=sum(x>.01000001 for x in v)) for k,v in checks.items()},input_hashes={str(p.relative_to(P)):sha(p) for p in files if p.exists()})
    save('coverage.json',out);print(json.dumps(out),flush=True)
    return z,mm
def load():return np.load(INPUT/'rows.npy'),json.loads((INPUT/'markets.json').read_text())
def weights(z):
    _,ix,n=np.unique(z[:,0],return_inverse=True,return_counts=True)
    return 1/n[ix]
def fit():
    z,_=load();params={};arrays={}
    np.save(INPUT/'fit_rows.npy',z[z[:,14]<2])
    for ai,a in enumerate(ASSETS):
        train=z[(z[:,14]==0)&(z[:,15]==ai)];cal=z[(z[:,14]==1)&(z[:,15]==ai)]
        x,c=empirical_fit(train[:,6],weights(train));p=empirical_probability(cal[:,5],x,c);mid=(cal[:,7]+cal[:,8])/2
        beta=fit_blend(mid,p,cal[:,13],weights(cal));arrays[a+'_x']=x;arrays[a+'_c']=c
        params[a]=dict(coefficients=beta.tolist(),distribution_markets=len(np.unique(train[:,0])),calibration_markets=len(np.unique(cal[:,0])),distribution_rows=len(train),calibration_rows=len(cal),absolute_residual_quantiles=np.quantile(abs(train[:,6]),[.5,.9,.95,.99]).tolist())
    np.savez_compressed(P/'distributions.npz',**arrays)
    save('models.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=sha(P/'PLAN.md'),model_sha256=sha(P/'model.py'),distribution_sha256=sha(P/'distributions.npz'),fit_rows_sha256=sha(INPUT/'fit_rows.npy'),fitted_parameters=params,fit_max_close_exclusive=DATES[2],scope='Frozen before later-period probability evaluation; previously studied history'))
    print(json.dumps(params),flush=True)
def predict(z):
    spec=json.loads((P/'models.json').read_text());assert spec['plan_sha256']==sha(P/'PLAN.md') and spec['model_sha256']==sha(P/'model.py') and spec['distribution_sha256']==sha(P/'distributions.npz')
    dist=np.load(P/'distributions.npz');out=dict(market=(z[:,7]+z[:,8])/2,gaussian=gaussian(z[:,5]),empirical=np.empty(len(z)),blended=np.empty(len(z)))
    for ai,a in enumerate(ASSETS):
        mask=z[:,15]==ai;physical=empirical_probability(z[mask,5],dist[a+'_x'],dist[a+'_c']);out['empirical'][mask]=physical;out['blended'][mask]=blend(out['market'][mask],physical,spec['fitted_parameters'][a]['coefficients'])
    return out
def calibration(z,pp):
    out={}
    for ai,a in enumerate(ASSETS):
        out[a]={}
        for ci,co in enumerate(PERIODS):
            out[a][co]={}
            for scope in ('all','band55_65'):
                mask=(z[:,15]==ai)&(z[:,14]==ci);favorite=(z[:,7]+z[:,8]>1);ask=np.where(favorite,z[:,8],1-z[:,7])
                if scope=='band55_65':mask&=(ask>=.55-1e-8)&(ask<=.65+1e-8)&(z[:,8]-z[:,7]<=.02000001)
                zz=z[mask]
                if not len(zz):continue
                w=weights(zz);y=zz[:,13];result={}
                if scope=='band55_65':y=np.where(favorite[mask],y,1-y)
                for kind,pr in pp.items():
                    p=pr[mask]
                    if scope=='band55_65':p=np.where(favorite[mask],p,1-p)
                    p=np.clip(p,.0001,.9999)
                    bins=[]
                    for lo in np.arange(0,1,.1):
                        h=(p>=lo)&(p<lo+.1)
                        if h.any():bins.append(dict(lo=round(float(lo),1),rows=int(h.sum()),market_weight=float(w[h].sum()),predicted=float(np.average(p[h],weights=w[h])),actual=float(np.average(y[h],weights=w[h]))))
                    result[kind]=dict(rows=len(p),markets=len(np.unique(zz[:,0])),brier=float(np.average((p-y)**2,weights=w)),logloss=float(np.average(-y*np.log(p)-(1-y)*np.log1p(-p),weights=w)),predicted=float(np.average(p,weights=w)),actual=float(np.average(y,weights=w)),reliability=bins)
                out[a][co][scope]=result
    save('calibration.json',out)
def trade_rows(z,mm,pp,kind,lo=.55,hi=.65,delay=False):
    out=[];entered=set()
    for i,r in enumerate(z):
        mi,t,rem,S,sig,threshold,residual,b,a,eb,ea,db,da,y,co,ai=r;mi=int(mi)
        if mi in entered:continue
        prob=pp[kind][i];options=[]
        for side,p,ask in ((1,prob,a),(-1,1-prob,1-b)):
            ok=qualifies(p,ask,a-b,lo,hi) if kind!='market' else (lo-1e-8<=ask<=hi+1e-8 and a-b<=.02000001)
            if ok:options.append((p-float(debit(ask)),side,p))
        if not options:continue
        _,side,p=max(options)
        fb,fa=(db,da) if delay else (eb,ea)
        if not np.isfinite(fb+fa) or fa-fb>.02000001 or (delay and rem<=1):continue
        price=fa if side==1 else 1-fb
        if not lo-1e-8<=price<=hi+1e-8:continue
        if kind!='market' and not qualifies(p,price,fa-fb,lo,hi):continue
        m=mm[mi];won=int(y if side==1 else 1-y);cash=float(debit(price));entered.add(mi)
        out.append(dict(ticker=m['ticker'],asset=m['asset'],cohort=m['cohort'],decision=int(t),entered=int(t)+(60 if delay else 3),close=m['close'],settled=m['settled'],rem=int(rem),side=side,entry=float(price),debit=cash,probability=float(p),won=won,pnl=won-cash-.0025,model=kind))
    return out
def metrics(rows,windows,ci,stress=.0025):
    rr=sorted(rows,key=lambda r:(r['settled'],r['ticker']));pnl=np.array([r['won']-r['debit']-stress for r in rr]);eq=np.r_[0,np.cumsum(pnl)]
    days=list(range(BOUNDS[ci],BOUNDS[ci+1],86400));daily={dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat():0. for t in days}
    for r,p in zip(rr,pnl):daily[dt.datetime.fromtimestamp(r['close'],dt.timezone.utc).date().isoformat()]+=float(p)
    n=len(rr);d=np.array(list(daily.values()));rng=np.random.default_rng(61003);boot=d[rng.integers(0,len(d),size=(10000,len(d)))].sum(axis=1)
    return dict(trades=n,available_windows=windows,coverage=n/windows if windows else 0,wins=sum(r['won'] for r in rr),losses=sum(1-r['won'] for r in rr),net=round(float(pnl.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),win_rate=float(np.mean([r['won'] for r in rr])) if n else None,modeled_probability=float(np.mean([r['probability'] for r in rr])) if n else None,break_even=float(np.mean([r['debit']+stress for r in rr])) if n else None,mean_entry=float(np.mean([r['entry'] for r in rr])) if n else None,daily=daily,day_bootstrap90=np.quantile(boot,[.05,.95]).tolist())
def evaluate():
    z,mm=load();pp=predict(z);np.savez_compressed(P/'forecasts.npz',**pp);calibration(z,pp);summary={}
    for kind in KINDS:
        for scope,lo,hi in [('band55_65',.55,.65),('broad10_90',.1,.9)]:
            if kind=='market' and scope!='band55_65':continue
            key=kind+'_'+scope;rr=trade_rows(z,mm,pp,kind,lo,hi);dd=trade_rows(z,mm,pp,kind,lo,hi,True);save('trades_'+key+'.json',rr);summary[key]={}
            for a in ASSETS:
                summary[key][a]={}
                for ci,co in enumerate(PERIODS):
                    sub=[r for r in rr if r['asset']==a and r['cohort']==co];delay=[r for r in dd if r['asset']==a and r['cohort']==co];den=sum(m['asset']==a and m['cohort']==co for m in mm)
                    summary[key][a][co]=dict(base=metrics(sub,den,ci),cost1c=metrics(sub,den,ci,.01),cost2c=metrics(sub,den,ci,.02),delay60=metrics(delay,den,ci))
            print(key,[(a,co,summary[key][a][co]['base']['trades'],summary[key][a][co]['base']['net']) for a in ASSETS for co in PERIODS[2:]],flush=True)
    save('results.json',summary)
if __name__=='__main__':{'build':build,'fit':fit,'evaluate':evaluate}[sys.argv[1]]()
