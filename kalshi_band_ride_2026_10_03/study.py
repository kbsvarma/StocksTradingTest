"""55–65c continuation/reversal study: public archived inputs, no order APIs."""
from pathlib import Path
import collections,datetime as dt,hashlib,itertools,json,math,sys
import numpy as np
P=Path(__file__).resolve().parent;ROOT=P.parent;SOURCE=ROOT/'kalshi_protected_profit_revisit_2026_10_03'
if (P/'inputs/market_arrays.npy').exists():SOURCE=P/'inputs'
PERIODS=('development','validation','later','october2','october3')
BOUNDS=[int(dt.datetime.fromisoformat(t+'T00:00:00+00:00').timestamp()) for t in ('2026-08-13','2026-08-28','2026-09-12','2026-10-02','2026-10-03')]
WINDOWS=((2,14),(12,14),(9,11),(6,8),(3,5),(1,2))
FILTERS=('none','z0','z025','z05','z075','z1','z15','z2','z_negative','z_below_minus025','momentum','quote_rising','quote_falling','forecast3','forecast6','z05_momentum')
ZBINS=((-99,0),(0,.25),(.25,.5),(.5,.75),(.75,1),(1,1.5),(1.5,2),(2,99))
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def period(t):return next((i for i in range(4) if BOUNDS[i]<=t<BOUNDS[i+1]),4)
def cash(p,n=1,precision=.0001):
    v=np.asarray(p);fee=np.ceil(.07*n*v*(1-v)*1e6-1e-8)/1e6
    return np.ceil((n*v+fee)/precision-1e-8)*precision
def load(recent=False):
    prefix='oct3_' if recent else '';x=np.load(SOURCE/(prefix+'arrays.npy' if recent else 'market_arrays.npy'));mm=json.loads((SOURCE/(prefix+'markets.json' if recent else 'markets.json')).read_text());return x,mm

def features(x,mm,delay=False):
    b,a=x[:,:,0],x[:,:,1];side=np.where(b+a>1,1,-1);bid=np.where(side==1,b,1-a);ask=np.where(side==1,a,1-b);off=4 if delay else 2;eb,ea=x[:,:,off],x[:,:,off+1]
    entry=np.where(side==1,ea,1-eb);opposite=np.where(side==1,1-eb,ea)
    z=x[:,:,6]*side;mom=x[:,:,7]*side;prob=np.where(side==1,x[:,:,8],1-x[:,:,8]);mid=(a+b)/2;previous=np.roll(mid,1,axis=1);previous[:,0]=np.nan;delta=side*(mid-previous)
    signal_valid=np.isfinite(b+a)&(b>=0)&(a<=1)&(a>=b)&(a-b<=.02000001)&(ask>=.55-1e-8)&(ask<=.65+1e-8)
    valid=signal_valid&np.isfinite(eb+ea)&(ea>=eb)&(ea-eb<=.02000001)&(eb>=0)&(ea<=1)&(entry>=.55-1e-8)&(entry<=.65+1e-8)
    results=np.array([m['result'] for m in mm]);wins=side==results[:,None];flip=np.full(side.shape,np.nan);dip=np.full(side.shape,np.nan);nobs=np.zeros(side.shape,int)
    for j in range(14):
        begin=j+1+int(delay)
        if begin>=14:continue
        obs=np.isfinite(mid[:,begin:]);nobs[:,j]=obs.sum(axis=1);s=side[:,j,None]
        fell=obs&((mid[:,begin:]-.5)*s< -1e-8)
        futurebid=np.where(s==1,b[:,begin:],1-a[:,begin:]);dd=obs&(futurebid<=entry[:,j,None]-.1+1e-8)
        flip[:,j]=np.where(nobs[:,j]>0,fell.any(axis=1),np.nan);dip[:,j]=np.where(nobs[:,j]>0,dd.any(axis=1),np.nan)
    return dict(x=x,mm=mm,side=side,bid=bid,ask=ask,entry=entry,opposite=opposite,z=z,mom=mom,prob=prob,delta=delta,valid=valid,signal_valid=signal_valid,wins=wins,flip=flip,dip=dip,nobs=nobs,co=np.array([period(m['close']) for m in mm]),asset=np.array([m['asset'] for m in mm]),delay=delay)

def mask(c,d):
    rem=np.arange(14,0,-1);v=d['valid']&(rem>=c['remlo'])&(rem<=c['remhi'])&(d['asset'][:,None]==c['asset']);f=c.get('filter','none');z=d['z']
    levels={'z0':0,'z025':.25,'z05':.5,'z075':.75,'z1':1,'z15':1.5,'z2':2}
    if f in levels:v=v&(z>=levels[f])
    elif f=='z_negative':v=v&(z<0)
    elif f=='z_below_minus025':v=v&(z<=-.25)
    elif f in ('momentum','z05_momentum'):v=v&(z>=(.5 if f=='z05_momentum' else 0))&(d['mom']>=.25)
    elif f=='quote_rising':v=v&(d['delta']>=.02-1e-8)
    elif f=='quote_falling':v=v&(d['delta']<=-c.get('drop',.02)+1e-8)
    elif f in ('forecast3','forecast6'):v=v&(d['prob']-d['ask']>=(.03 if f=='forecast3' else .06))
    if 'zlo' in c:v=v&(z>=c['zlo'])&(z<c['zhi'])
    return v
def indices(c,d):
    v=mask(c,d);i=np.flatnonzero(v.any(axis=1));j=v[i].argmax(axis=1);return i,j
def rows(c,d):
    i,j=indices(c,d);out=[]
    for mi,ji in zip(i,j):
        m=d['mm'][mi];favside=int(d['side'][mi,ji]);direction=favside*c['direction'];entry=float(d['entry'][mi,ji] if c['direction']==1 else d['opposite'][mi,ji]);won=int(direction==m['result']);T=m['start']+60*(int(ji)+1)
        r=dict(ticker=m['ticker'],asset=m['asset'],cohort=PERIODS[d['co'][mi]],close=m['close'],settled=max(m['close']+30,m['settled']),decision=T,entered=T+(60 if d['delay'] else 3),rem=14-int(ji),favside=favside,side=direction,entry=entry,debit=float(cash(entry)),won=won,favorite_won=int(d['wins'][mi,ji]),z=float(d['z'][mi,ji]) if np.isfinite(d['z'][mi,ji]) else None,momentum=float(d['mom'][mi,ji]) if np.isfinite(d['mom'][mi,ji]) else None,subsequent_observations=int(d['nobs'][mi,ji]),crossed_below_half=bool(d['flip'][mi,ji]) if np.isfinite(d['flip'][mi,ji]) else None,dipped_ten_cents=bool(d['dip'][mi,ji]) if np.isfinite(d['dip'][mi,ji]) else None)
        r['pnl']=won-r['debit'];out.append(r)
    return out

def metrics(rr,den,stress=.0025,bootstrap=False):
    rr=sorted(rr,key=lambda r:(r['settled'],r['ticker']));n=len(rr);pp=np.array([r['pnl']-stress for r in rr]);eq=np.r_[0,np.cumsum(pp)];valid=[r for r in rr if r['crossed_below_half'] is not None];flips=[r for r in valid if r['crossed_below_half']];daily=collections.defaultdict(float)
    for r,p in zip(rr,pp):daily[dt.datetime.fromtimestamp(r['close'],dt.timezone.utc).date().isoformat()]+=float(p)
    out=dict(trades=n,windows=den,coverage=n/den if den else 0,wins=sum(r['won'] for r in rr),win_rate=sum(r['won'] for r in rr)/n if n else None,final_reversal_rate=sum(not r['favorite_won'] for r in rr)/n if n else None,mean_entry=float(np.mean([r['entry'] for r in rr])) if n else None,break_even_win_rate=float(np.mean([r['debit']+stress for r in rr])) if n else None,net=round(float(pp.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),observed_path_markets=len(valid),crossed_half=len(flips),crossed_half_rate=len(flips)/len(valid) if valid else None,flipped_then_won=sum(r['favorite_won'] for r in flips),recovery_rate_given_flip=sum(r['favorite_won'] for r in flips)/len(flips) if flips else None,ten_cent_dips=sum(bool(r['dipped_ten_cents']) for r in valid),daily=dict(daily))
    if bootstrap and daily:
        dd=np.array(list(daily.values()));rng=np.random.default_rng(52632);vals=dd[rng.integers(0,len(dd),size=(10000,len(dd)))].sum(axis=1);out['day_bootstrap_90pct_net']=np.quantile(vals,[.05,.95]).tolist()
    return out
def configs():
    return [dict(id=i,asset=a,direction=direction,remlo=w[0],remhi=w[1],filter=f) for i,(a,direction,w,f) in enumerate(itertools.product(('BTC','ETH'),(1,-1),WINDOWS,FILTERS))]
def select():
    x,mm=load();d=features(x,mm);cc=configs();save('configurations.json',cc);res={};chosen=[]
    for c in cc:
        r=rows(c,d);res[c['id']]={co:metrics([v for v in r if v['cohort']==co],sum(m['asset']==c['asset'] and PERIODS[period(m['close'])]==co for m in mm),.01) for co in PERIODS[:2]}
    save('early_screen.json',res)
    for a,di in itertools.product(('BTC','ETH'),(1,-1)):
        eligible=[c for c in cc if c['asset']==a and c['direction']==di and all(res[c['id']][co]['trades']>=100 and res[c['id']][co]['coverage']>=.10 for co in PERIODS[:2])]
        passing=[c for c in eligible if all(res[c['id']][co]['net']>0 for co in PERIODS[:2])];best=max(passing or eligible,key=lambda c:res[c['id']]['validation']['net'])
        chosen.append(dict(config=best,passing=len(passing),eligible=len(eligible),early=res[best['id']]))
    save('frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=sha(P/'PLAN.md'),configuration_sha256=sha(P/'configurations.json'),selected=chosen,trials=len(cc)))
    print(json.dumps(chosen,indent=2),flush=True)

def evaluate():
    x,mm=load();d=features(x,mm);delay=features(x,mm,True);frozen=json.loads((P/'frozen_selection.json').read_text());assert frozen['plan_sha256']==sha(P/'PLAN.md');cc={r['config']['id']:r['config'] for r in frozen['selected']}
    cc.update({c['id']:c for c in configs() if c['remlo']==2 and c['remhi']==14 and c['filter']=='none'});out={}
    for cid,c in cc.items():
        rr=rows(c,d);save(f'trades_{cid}.json',rr);delayed=rows(c,delay);v={}
        for co in PERIODS[:4]:
            den=sum(m['asset']==c['asset'] and PERIODS[period(m['close'])]==co for m in mm);r=[v for v in rr if v['cohort']==co]
            v[co]=dict(base=metrics(r,den,bootstrap=True),cost1c=metrics(r,den,.01),cost2c=metrics(r,den,.02),delay=metrics([v for v in delayed if v['cohort']==co],den))
        out[cid]=dict(config=c,periods=v);print(cid,c,[(co,v[co]['base']['trades'],v[co]['base']['net']) for co in PERIODS[:4]],flush=True)
    save('results.json',out)
    tables={}
    for a in ('BTC','ETH'):
        cells=[]
        for window in WINDOWS:
            c=dict(asset=a,direction=1,remlo=window[0],remhi=window[1],filter='none')
            for zb in (None,*ZBINS):
                cfg=dict(c,**(dict(zlo=zb[0],zhi=zb[1]) if zb else {}));rr=rows(cfg,d)
                cells.append(dict(config=cfg,periods={co:metrics([r for r in rr if r['cohort']==co],sum(m['asset']==a and PERIODS[period(m['close'])]==co for m in mm)) for co in PERIODS[:4]}))
        tables[a]=cells
    save('time_z_tables.json',tables)
    xr,mr=load(True);dr=features(xr,mr);recent={}
    for cid,c in cc.items():
        rr=rows(c,dr);save(f'oct3_quote_trades_{cid}.json',rr);recent[cid]=metrics(rr,sum(m['asset']==c['asset'] for m in mr))
    save('oct3_quote_results.json',recent)

if __name__=='__main__':select() if sys.argv[1]=='select' else evaluate()
