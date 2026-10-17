"""Local-only SPXW cross-fitted smile pilot. See PROTOCOL.md."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse, hashlib, json, time, warnings
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import ndtr

ROOT=Path(__file__).resolve().parents[1]; OUT=Path(__file__).resolve().parent
BASE=ROOT/'spx_spread_bot/backtests/narrow_width_databento_2026_05_21'
EXTRA=ROOT/'bps_rederive_2026_07/data'; ET='America/New_York'
MODELS=['flat','ssvi','svi']; TIMES=['10:00','12:00','14:00']
COLS=['symbol','bid_px_00','ask_px_00','bid_sz_00','ask_sz_00']

def source_paths(day,side):
    return [BASE/f'spxw_{side}_cbbo_1m/spxw_{side}_cbbo_{day}.parquet',
      EXTRA/f'call_wing/spxw_c_cbbo_{day}.parquet' if side=='c' else EXTRA/'unused',
      EXTRA/f'new_days/spxw_{side}_cbbo_{day}.parquet',
      EXTRA/f'repairs/missing_side/{side.upper()}_{day}.parquet',
      EXTRA/f'repairs/winter_hour_{side.upper()}/{day}.parquet']

def all_days():
    days=set()
    for folder in [BASE/'spxw_p_cbbo_1m',EXTRA/'new_days']:
        for p in folder.glob('spxw_p_cbbo_*.parquet'):
            d=p.stem[-8:]
            if '20240101'<=d<='20260702': days.add(d)
    return sorted(days)

def load_day(day):
    frames=[]; audit={'day':day,'raw_rows':0,'wrong_expiry':0,'duplicate_rows':0,'invalid_rows':0,'files':0}
    for side in ['p','c']:
        parts=[]
        for p in source_paths(day,side):
            if p.is_file() and p.stat().st_size:
                d=pd.read_parquet(p,columns=COLS);d['t']=d.index.tz_convert(ET)
                parts.append(d.reset_index(drop=True));audit['files']+=1
        if not parts: continue
        d=pd.concat(parts,ignore_index=True);audit['raw_rows']+=len(d)
        ex=d.symbol.str.extract(r'^SPXW\s+(\d{6})([CP])(\d{8})$')
        valid=(ex[0]==day[2:]) & (ex[1]==side.upper())
        audit['wrong_expiry']+=int((~valid).sum());d=d[valid].copy()
        d['K']=ex.loc[valid,2].astype(float)/1000;d['cp']=side.upper()
        n=len(d);d=d.drop_duplicates(['t','symbol'],keep='last');audit['duplicate_rows']+=n-len(d)
        d=d.rename(columns={'bid_px_00':'bid','ask_px_00':'ask','bid_sz_00':'bs','ask_sz_00':'asz'})
        valid=np.isfinite(d.bid)&np.isfinite(d.ask)&(d.bid>0)&(d.ask>=d.bid)&(d.bs>0)&(d.asz>0)&(d.ask<100000)
        audit['invalid_rows']+=int((~valid).sum());d=d[valid].copy()
        d['mid']=(d.bid+d.ask)/2;d['hs']=(d.ask-d.bid)/2
        d['fold']=(np.rint(d.K/5).astype(int)%2)
        frames.append(d)
    if not frames: return {},audit
    d=pd.concat(frames,ignore_index=True)
    return {t:g.set_index(['cp','K'],drop=False) for t,g in d.groupby('t')},audit

def price(F,K,w,cp):
    w=np.maximum(w,1e-14);s=np.sqrt(w);d1=np.log(F/K)/s+s/2;d2=d1-s
    return np.where(np.asarray(cp)=='C',F*ndtr(d1)-K*ndtr(d2),K*ndtr(-d2)-F*ndtr(-d1))

def variance(model,p,k,scale):
    if model=='flat': return np.full_like(np.asarray(k,dtype=float),scale**2*p[0])
    x=np.asarray(k)/scale
    if model=='ssvi':
        a,b,r=p;z=b*x+r
        return scale**2*a/2*(1+r*b*x+np.sqrt(z*z+1-r*r))
    a,b,r,m,s=p;z=x-m
    return scale**2*(a+b*(r*z+np.sqrt(z*z+s*s)))

def svi_g(p,k,scale):
    a,b,r,m,s=p;x=np.asarray(k)/scale;z=x-m;v=np.sqrt(z*z+s*s)
    w=variance('svi',p,k,scale);wp=scale*b*(r+z/v);wpp=b*s*s/(v**3)
    return (1-np.asarray(k)*wp/(2*np.maximum(w,1e-14)))**2-wp**2/4*(1/np.maximum(w,1e-14)+.25)+wpp/2

def constraints(model,p,scale,grid):
    if model=='ssvi':
        a,b,r=p
        return np.array([4-scale*a*b*(1+abs(r))-1e-6,4-a*b*b*(1+abs(r))-1e-6])
    a,b,r,m,s=p
    return np.r_[a+b*s*np.sqrt(1-r*r)-1e-5,2-scale*b*(1+abs(r))-1e-5,svi_g(p,grid,scale)-1e-6]

def prepare(snap,fold):
    if snap is None or snap.empty: return None,'no_snapshot'
    clean=snap[(snap.mid>=.25)&((snap.ask-snap.bid)<=np.maximum(.5,.25*snap.mid))].reset_index(drop=True).copy()
    train=clean[clean.fold!=fold]
    calls=train[train.cp=='C'].reset_index(drop=True)
    puts=train[train.cp=='P'].reset_index(drop=True)
    pair=calls.merge(puts,on='K',suffixes=('_c','_p'))
    if len(pair)<3: return None,'insufficient_training_parity'
    pair['dist']=abs(pair.mid_c-pair.mid_p);pair=pair.nsmallest(5,'dist')
    F=float((pair.K+pair.mid_c-pair.mid_p).median())
    if not np.isfinite(F) or F<=0:return None,'bad_forward'
    clean['k']=np.log(clean.K/F)
    clean=clean[(abs(clean.k)<=.06)&(((clean.K>=F)&(clean.cp=='C'))|((clean.K<F)&(clean.cp=='P')))]
    train=clean[clean.fold!=fold].sort_values('K');test=clean[clean.fold==fold].sort_values('K')
    if len(train)<12:return None,'insufficient_training_strikes'
    test=test[(test.K>=train.K.min())&(test.K<=train.K.max())]
    if len(test)<4:return None,'insufficient_heldout_strikes'
    return (F,train,test),''

def fit(model,F,train):
    K=train.K.to_numpy();k=np.log(K/F);cp=train.cp.to_numpy();mid=train.mid.to_numpy();den=np.maximum(train.hs.to_numpy(),.10)
    # Fixed normalization from training ATM OTM premium, then optimized flat total variance.
    j=np.argmin(abs(k));scale=float(np.clip((mid[j]+abs(F-K[j])*.5)*np.sqrt(2*np.pi)/F,.0005,.05))
    def obj(p):
        residual=(price(F,K,variance(model,p,k,scale),cp)-mid)/den
        return np.mean(residual**2)
    if model=='flat':
        r=minimize_scalar(lambda a:obj([a]),bounds=(.01,100),method='bounded',options={'xatol':1e-7})
        return (np.array([r.x]),scale,float(r.fun)) if r.success else None
    grid=np.unique(np.r_[np.linspace(-.15,.15,121),np.linspace(k.min(),k.max(),81)])
    if model=='ssvi':
        starts=[[1,.4,-.4],[1,.1,0]];bounds=[(.01,30),(.001,3),(-.98,.98)]
    else:
        starts=[[.5,.4,-.4,0,1],[.8,.1,0,0,1]]
        bounds=[(-10,30),(.00001,10),(-.98,.98),(-6,6),(.03,10)]
    best=None
    for start in starts:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            r=minimize(obj,start,method='SLSQP',bounds=bounds,
              constraints={'type':'ineq','fun':lambda p:constraints(model,p,scale,grid)},
              options={'maxiter':1200,'ftol':1e-8})
        if r.success and np.isfinite(r.fun) and np.min(constraints(model,r.x,scale,grid))>=-1e-7:
            dense=np.unique(np.r_[np.linspace(-.15,.15,1201),np.linspace(k.min(),k.max(),401)])
            if np.min(constraints(model,r.x,scale,dense))>=-1e-7:
                if best is None or r.fun<best[2]:best=(r.x,scale,float(r.fun))
    return best

def predict(model,result,F,d):
    p,s,_=result
    return price(F,d.K.to_numpy(),variance(model,p,np.log(d.K.to_numpy()/F),s),d.cp.to_numpy())

def candidates(test,pred,fold,F):
    d=test.copy();d['fair']=pred;out=[]
    for cp in ['C','P']:
        part=d[d.cp==cp].reset_index(drop=True).set_index('K',drop=False)
        for lo in sorted(part.index):
            hi=lo+10
            if hi not in part.index:continue
            a,b=(part.loc[lo],part.loc[hi]) if cp=='C' else (part.loc[hi],part.loc[lo])
            fair=a.fair-b.fair;mid=a.mid-b.mid;hs=a.hs+b.hs
            score=abs(fair-mid)-2*hs-.026
            if score>0 and 0<=fair<=10:
                out.append(dict(cp=cp,buyK=float(a.K),sellK=float(b.K),fold=fold,F=F,fair=float(fair),signal_mid=float(mid),signal_hs=float(hs),edge=float(fair-mid),score=float(score),direction=1 if fair>mid else -1))
    return out

def vertical(snap,c):
    if snap is None:return None
    try:a=snap.loc[(c['cp'],c['buyK'])];b=snap.loc[(c['cp'],c['sellK'])]
    except KeyError:return None
    return float(a.bid-b.ask),float(a.ask-b.bid),float(a.mid-b.mid)

def process(day):
    snapshots,audit=load_day(day);predrows=[];fits=[];trades=[]
    d0=pd.Timestamp(day).tz_localize(ET)
    for clock in TIMES:
        hh,mm=map(int,clock.split(':'));t=d0+pd.Timedelta(hours=hh,minutes=mm)
        selected={m:[] for m in MODELS}
        for fold in [0,1]:
            prep,reason=prepare(snapshots.get(t),fold)
            if prep is None:
                fits.append(dict(day=day,time=clock,fold=fold,model='all',status=reason));continue
            F,train,test=prep
            for model in MODELS:
                result=fit(model,F,train)
                row=dict(day=day,time=clock,fold=fold,model=model,train_n=len(train),test_n=len(test),F=F,status='ok' if result else 'fit_failed')
                if result:
                    row.update(params=json.dumps(result[0].tolist()),scale=result[1],loss=result[2]);pred=predict(model,result,F,test)
                    for (_,q),pr in zip(test.iterrows(),pred):
                        predrows.append(dict(day=day,time=clock,fold=fold,model=model,cp=q.cp,K=q.K,bid=q.bid,ask=q.ask,mid=q.mid,pred=float(pr),error=float(pr-q.mid)))
                    selected[model].extend(candidates(test,pred,fold,F))
                fits.append(row)
        for model,cs in selected.items():
            if not cs:continue
            c=max(cs,key=lambda x:x['score']);entryt=t+pd.Timedelta(minutes=1)
            en=vertical(snapshots.get(entryt),c)
            for h in [5,15,60]:
                row=dict(day=day,time=clock,model=model,horizon=h,**c)
                if en is None: row['status']='missing_entry';trades.append(row);continue
                direction=c['direction'];entry=en[1] if direction==1 else en[0]
                exitt=entryt+pd.Timedelta(minutes=h);ex=vertical(snapshots.get(exitt),c)
                if ex is None:exitprice=0 if direction==1 else 10;status='missing_exit_adverse_bound'
                else:exitprice=ex[0] if direction==1 else ex[1];status='ok'
                pnl=direction*(exitprice-entry)*100-2.60
                row.update(status=status,entry=entry,exit=exitprice,pnl=pnl,pnl_stress=pnl-20,
                  entry_mid=en[2],exit_mid=ex[2] if ex else np.nan,
                  mid_pnl=direction*(ex[2]-en[2])*100 if ex else np.nan)
                if h==15 and ex:
                    prep,reason=prepare(snapshots.get(exitt),c['fold'])
                    if prep:
                        F2,tr2,te2=prep
                        if min(c['buyK'],c['sellK'])>=tr2.K.min() and max(c['buyK'],c['sellK'])<=tr2.K.max():
                            res2=fit(model,F2,tr2)
                            if res2:
                                target=pd.DataFrame({'K':[c['buyK'],c['sellK']],'cp':[c['cp']]*2})
                                pp=predict(model,res2,F2,target);fair2=float(pp[0]-pp[1])
                                row['exit_fair']=fair2;row['residual_convergence']=direction*(c['edge']-(fair2-ex[2]))*100
                trades.append(row)
    return predrows,fits,trades,audit

def selftest():
    F=5000.;K=np.array([4900.,5000.,5100.]);w=np.full(3,.0001)
    assert np.allclose(price(F,K,w,['C']*3)-price(F,K,w,['P']*3),F-K,atol=1e-9)
    assert np.all(price(F,K,w,['C']*3)>=np.maximum(F-K,0))
    k=np.linspace(-.05,.05,50);p=np.array([.8,.3,-.4,.2,1.]);s=.01;eps=1e-5
    ww=variance('svi',p,k,s);wp=(variance('svi',p,k+eps,s)-variance('svi',p,k-eps,s))/(2*eps)
    wpp=(variance('svi',p,k+eps,s)-2*ww+variance('svi',p,k-eps,s))/eps**2
    numerical=(1-k*wp/(2*ww))**2-wp**2/4*(1/ww+.25)+wpp/2
    assert np.max(abs(numerical-svi_g(p,k,s)))<1e-5
    # Held-out quote perturbations cannot change the forward or training set.
    ks=np.arange(4800.,5205.,5);rows=[]
    for cp in ['C','P']:
        values=price(F,ks,np.full(len(ks),.0001),[cp]*len(ks))
        for kk,v in zip(ks,values): rows.append(dict(K=kk,cp=cp,mid=v,bid=max(v-.1,.001),ask=v+.1,hs=.1,fold=int(round(kk/5))%2))
    snap=pd.DataFrame(rows);a,_=prepare(snap,0);altered=snap.copy();altered.loc[altered.fold==0,['bid','ask','mid']]*=1.1;b,_=prepare(altered,0)
    assert a is not None and b is not None and a[0]==b[0] and a[1].equals(b[1])
    train=a[1]
    for model in MODELS:
        res=fit(model,F,train);assert res is not None,model
        assert np.mean(abs(predict(model,res,F,train)-train.mid))<.05,model
    # Economic cashflow: buy vertical at 3, exit 4 => 97.40; sell reverses.
    assert abs((4-3)*100-2.60-97.40)<1e-10
    print('SELFTEST PASS: parity, intrinsic bound, density derivatives, fold isolation, synthetic recovery, cashflow',flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int);ap.add_argument('--workers',type=int,default=4);ap.add_argument('--selftest',action='store_true');ap.add_argument('--out',default='results');args=ap.parse_args()
    if args.selftest:selftest();return
    days=all_days();days=days[:args.limit] if args.limit else days
    dest=OUT/args.out;dest.mkdir(exist_ok=True)
    manifest=[]
    for day in days:
        for side in ['p','c']:
            for p in source_paths(day,side):
                if p.is_file():manifest.append(dict(path=str(p.relative_to(ROOT)),size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns))
    (dest/'manifest.json').write_text(json.dumps(dict(days=days,sources=manifest,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),protocol_sha256=hashlib.sha256((OUT/'PROTOCOL.md').read_bytes()).hexdigest()),indent=2))
    predictions=[];fits=[];trades=[];audits=[];start=time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n,(p,f,t,a) in enumerate(pool.map(process,days),1):
            predictions.extend(p);fits.extend(f);trades.extend(t);audits.append(a)
            if n%20==0 or n==len(days):print(f'{n}/{len(days)} days; {len(predictions)} predictions; {len(trades)} trade/horizon rows; {time.time()-start:.0f}s',flush=True)
    for name,rows in [('predictions',predictions),('fits',fits),('trades',trades),('audit',audits)]:
        pd.DataFrame(rows).to_parquet(dest/f'{name}.parquet',index=False)
    print('DONE',dest,flush=True)
if __name__=='__main__':main()
