"""Offline protected-profit experiment. No credentials, trading client or orders."""
from pathlib import Path
import collections, datetime as dt, hashlib, itertools, json, math, pickle, sys
import numpy as np
from numba import njit
P=Path(__file__).resolve().parent;ROOT=P.parent
SOURCE=ROOT/'kalshi_every_window_2026_10_03/prepared.pickle'
if (P/'input_sources/prepared.pickle').exists():SOURCE=P/'input_sources/prepared.pickle'
BOUNDS=[int(dt.datetime.fromisoformat(x+'T00:00:00+00:00').timestamp()) for x in ('2026-08-13','2026-08-28','2026-09-12','2026-10-02','2026-10-03')]
PERIODS=('development','validation','later','october2','october3')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cohort(t):return next((i for i in range(4) if BOUNDS[i]<=t<BOUNDS[i+1]),4)
def q(c,field):
    if not c:return None
    try:b,a=float(c['b'+field]),float(c['a'+field])
    except (KeyError,TypeError,ValueError):return None
    return (b,a) if 0<=b<=a<=1 else None

def prepare():
    mm=pickle.loads(SOURCE.read_bytes())
    mm=sorted(mm,key=lambda m:(m['close'],m['asset']))
    x=np.full((len(mm),14,10),np.nan);meta=[]
    for i,m in enumerate(mm):
        for j,rem in enumerate(range(14,0,-1)):
            T=m['close']-60*rem;c=q(m['candles'].get(T),'c');o=q(m['candles'].get(T+60),'o');d=q(m['candles'].get(T+60),'c') if rem>1 else None
            if c:x[i,j,:2]=c
            if o:x[i,j,2:4]=o
            if d:x[i,j,4:6]=d
            f=m['new_features'].get(rem,{})
            x[i,j,6]=f.get('z',np.nan);x[i,j,7]=f.get('xrich',[np.nan]*7)[6];x[i,j,8]=f.get('prich',np.nan)
            x[i,j,9]=f.get('sigma',np.nan)
        meta.append({k:m[k] for k in ('ticker','asset','start','close','settled','result')})
    np.save(P/'market_arrays.npy',x);save('markets.json',meta)
    models=P/'input_sources/models.json' if (P/'input_sources/models.json').exists() else ROOT/'kalshi_every_window_2026_10_03/models.json'
    save('input_manifest.json',dict(plan_sha256=sha(P/'PLAN.md'),source=str(SOURCE),source_sha256=sha(SOURCE),models_sha256=sha(models),counts=dict(collections.Counter(m['asset']+'_'+PERIODS[cohort(m['close'])] for m in meta)),model_note='Frozen rich logistic coefficients fitted Aug13–27 only in earlier every-window study; reused transparently. Entire history is previously researched.'))

def configs():
    exits=[dict(name='hold',mode=0,stop=0,safe=0,ideal=0,buffer=0),dict(name='stop45',mode=1,stop=.45,safe=9,ideal=9,buffer=0),dict(name='old45_6_13',mode=1,stop=.45,safe=.06,ideal=.13,buffer=0)]
    for stop,safe,ideal,buffer in itertools.product((.10,.15,.20),(.06,.10),(.13,.20),(0,.03,.06)):
        exits.append(dict(name=f'protected_{stop}_{safe}_{ideal}_{buffer}',mode=2,stop=stop,safe=safe,ideal=ideal,buffer=buffer))
    exits.extend([dict(name='time_scaled',mode=3,stop=.15,safe=.06,ideal=.13,buffer=.03),dict(name='trailing',mode=4,stop=.15,safe=.06,ideal=.13,buffer=.03)])
    out=[]
    for asset,band,window,fil in itertools.product(('BTC','ETH'),((.55,.65),(.60,.70),(.50,.70)),((2,14),(9,14),(5,8),(2,4)),range(5)):
        for ex in exits:
            out.append(dict(id=len(out),asset=asset,lo=band[0],hi=band[1],remlo=window[0],remhi=window[1],filter=fil,**ex))
    return out

@njit(cache=True)
def cashchange(p,n,buy,precision=.0001):
    raw=math.ceil(.07*n*p*(1-p)*1e6-1e-8)/1e6
    revenue=-n*p if buy else n*p
    return math.floor((revenue-raw)/precision+1e-8)*precision

@njit(cache=True)
def simulate_market(x,result,c,delay=0,quantity=1,precision=.0001):
    # Return entry index, side, entry, exit, exit index, reason, safe/ideal armed.
    lo,hi,rlo,rhi,fil,mode,stop,safe,ideal,buffer=c
    for j in range(14):
        rem=14-j;b,a=x[j,0],x[j,1]
        if not rlo<=rem<=rhi or np.isnan(b) or np.isnan(a):continue
        side=1 if b+a>1 else -1
        bid=b if side==1 else 1-a;ask=a if side==1 else 1-b
        if ask<lo-1e-8 or ask>hi+1e-8 or ask-bid>.02000001:continue
        z=x[j,6]*side;mom=x[j,7]*side;p=x[j,8] if side==1 else 1-x[j,8]
        if fil==1 and (np.isnan(z) or z<.25):continue
        if fil==2 and (np.isnan(z) or np.isnan(mom) or z<0 or mom<.25):continue
        if fil>=3 and (np.isnan(p) or p-ask<(.03 if fil==3 else .06)):continue
        off=4 if delay else 2;eb,ea=x[j,off],x[j,off+1]
        if np.isnan(eb) or np.isnan(ea) or ea-eb>.02000001:continue
        entry=ea if side==1 else 1-eb
        if not lo-1e-8<=entry<=hi+1e-8:continue
        scale=max(.7,min(1.4,math.sqrt(rem/7))) if mode==3 else 1.
        floor=stop if mode==1 else entry-stop*scale
        sy=entry+safe*scale;iy=entry+ideal*scale;buf=buffer*scale
        armed=0;peak=entry;pending=0
        if mode!=0:
            for k in range(j+1+delay,14):
                qb,qa=x[k,0],x[k,1]
                if not np.isnan(qb) and not np.isnan(qa) and not pending:
                    price=qb if side==1 else 1-qa
                    protect=(iy if armed==2 else sy)-buf
                    if mode==4 and armed==2:protect=max(protect,peak-.06)
                    if price<=floor+1e-8:pending=1
                    elif armed and price<=protect+1e-8:pending=2 if armed==1 else 3
                    else:
                        peak=max(peak,price)
                        if price>=iy-1e-8:armed=2
                        elif price>=sy-1e-8 and armed<1:armed=1
                if pending:
                    xb,xa=x[k,off],x[k,off+1]
                    if not np.isnan(xb) and not np.isnan(xa):
                        price=xb if side==1 else 1-xa
                        if 0<price<1:return (j,side,entry,price,k,pending,int(armed>=1),int(armed==2))
        return (j,side,entry,1.0 if result==side else 0.0,14,0,int(armed>=1),int(armed==2))
    return (-1,0,0.,0.,-1,0,0,0)

def carray(c):return np.array([c[k] for k in ('lo','hi','remlo','remhi','filter','mode','stop','safe','ideal','buffer')],float)

@njit(cache=True)
def screen_kernel(x,labels,result,configs,assets):
    out=np.zeros((len(configs),2,5))
    for ci,c in enumerate(configs):
        for i in range(len(x)):
            co=int(labels[i,1])
            if co>1 or labels[i,0]!=assets[ci]:continue
            tr=simulate_market(x[i],result[i],c)
            if tr[0]<0:continue
            legs=1+int(tr[5]>0)
            pnl=cashchange(tr[2],1,True)+(cashchange(tr[3],1,False) if legs==2 else tr[3])-.0025*legs
            out[ci,co,0]+=1;out[ci,co,1]+=pnl;out[ci,co,2]+=int(pnl>0);out[ci,co,3]+=legs-1;out[ci,co,4]+=pnl*pnl
    return out

def select():
    x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());cc=configs();save('configurations.json',cc)
    labels=np.array([[int(m['asset']=='ETH'),cohort(m['close'])] for m in mm]);yy=np.array([m['result'] for m in mm])
    out=screen_kernel(x,labels,yy,np.array([carray(c) for c in cc]),np.array([int(c['asset']=='ETH') for c in cc]));np.save(P/'development_validation.npy',out)
    chosen=[];den=collections.Counter((m['asset'],cohort(m['close'])) for m in mm)
    for a in ('BTC','ETH'):
        for coverage in (.35,.15):
            candidates=[c for c in cc if c['asset']==a and c['mode']>=1 and c['name']!='stop45' and all(out[c['id'],co,0]>=max(200,coverage*den[a,co]) for co in (0,1))]
            passing=[c for c in candidates if all(out[c['id'],co,1]>0 for co in (0,1))]
            pick=max(passing or candidates,key=lambda c:out[c['id'],1,1]) if candidates else None
            chosen.append(dict(asset=a,min_coverage=coverage,passing=len(passing),eligible=len(candidates),passed=bool(passing),config=pick,early_stats=out[pick['id']].tolist() if pick else None))
    refs=[c for c in cc if c['name']=='old45_6_13' and c['lo'] in (.55,.60) and c['remlo']==2 and c['remhi']==14 and c['filter']==0]
    save('frozen_selection.json',dict(plan_sha256=sha(P/'PLAN.md'),configs_sha256=sha(P/'configurations.json'),selected=chosen,references=refs,trials=len(cc),frozen_at=dt.datetime.now(dt.timezone.utc).isoformat()))
    print(json.dumps(chosen,indent=2),flush=True)

def trades(c,x,mm,delay=0,quantity=1,precision=.0001):
    rows=[]
    for i,m in enumerate(mm):
        if m['asset']!=c['asset']:continue
        t=simulate_market(x[i],m['result'],carray(c),delay,quantity,precision)
        if t[0]<0:continue
        j,side,entry,price,k,reason,sa,ia=t;T=m['start']+60*(j+1);exit_ts=max(m['close']+30,m['settled']) if not reason else m['start']+60*(k+1)+(60 if delay else 3)
        debit=cashchange(entry,quantity,True,precision);credit=cashchange(price,quantity,False,precision) if reason else quantity*price
        rows.append(dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],cohort=PERIODS[cohort(m['close'])],decision=T,entered=T+(60 if delay else 3),entry=entry,side=int(side),exit=price,exit_ts=exit_ts,reason=('settlement','hard_stop','safe_reversal','ideal_reversal')[int(reason)],safe_armed=bool(sa),ideal_armed=bool(ia),quantity=quantity,legs=1+int(reason>0),debit=debit,credit=credit,pnl=debit+credit,hold_pnl=debit+quantity*int(m['result']==side)))
    return rows

def metrics(rows,den,stress=.0025,bootstrap=False):
    rr=sorted(rows,key=lambda r:(r['exit_ts'],r['ticker']));pp=np.array([r['pnl']-stress*r['quantity']*r['legs'] for r in rr]);eq=np.r_[0,np.cumsum(pp)];daily=collections.defaultdict(float)
    for r,p in zip(rr,pp):daily[dt.datetime.fromtimestamp(r['exit_ts'],dt.timezone.utc).date().isoformat()]+=float(p)
    out=dict(trades=len(rr),windows=den,coverage=len(rr)/den if den else 0,net=round(float(pp.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),positive=int((pp>1e-8).sum()),negative=int((pp< -1e-8).sum()),flat=int((abs(pp)<=1e-8).sum()),reasons=dict(collections.Counter(r['reason'] for r in rr)),safe_armed=sum(r['safe_armed'] for r in rr),ideal_armed=sum(r['ideal_armed'] for r in rr),hold_identical_entries_net=round(sum(r['hold_pnl']-stress*r['quantity'] for r in rr),6),daily=dict(daily),mean_entry=float(np.mean([r['entry'] for r in rr])) if rr else None)
    if bootstrap and daily:
        dd=np.array(list(daily.values()));rng=np.random.default_rng(41137);vals=dd[rng.integers(0,len(dd),size=(20000,len(dd)))].sum(axis=1)
        out['daily_bootstrap_total_90pct']=np.quantile(vals,[.05,.95]).tolist()
    return out

def portfolio(rows,stress=.0025):
    cash=100.;active=[];realized=collections.defaultdict(float);accepted=[];rejected=collections.Counter()
    def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat()
    for r in sorted(rows,key=lambda r:(r['entered'],r['ticker'])):
        still=[]
        for old in active:
            if old['exit_ts']<=r['entered']:
                cash+=old['credit']-stress*old['quantity']*(old['legs']-1);realized[day(old['exit_ts'])]+=old['pnl']-stress*old['quantity']*old['legs']
            else:still.append(old)
        active=still;cost=-r['debit']+stress*r['quantity'];risk=sum(-o['debit']+stress*o['quantity'] for o in active)
        if any(o['asset']==r['asset'] for o in active):rejected['asset_unsettled']+=1;continue
        if realized[day(r['entered'])]-risk-cost < -10-1e-8:rejected['daily_full_cost_risk']+=1;continue
        if cash<cost+5:rejected['cash']+=1;continue
        cash-=cost;active.append(r);accepted.append(r)
    return accepted,dict(rejected)

def evaluate():
    frozen=json.loads((P/'frozen_selection.json').read_text());assert frozen['plan_sha256']==sha(P/'PLAN.md')
    assert frozen['configs_sha256']==sha(P/'configurations.json')
    x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());cc={s['config']['id']:s['config'] for s in frozen['selected'] if s['config']}
    cc.update({c['id']:c for c in frozen['references']});out={}
    for cid,c in cc.items():
        rows=trades(c,x,mm);save(f'trades_{cid}.json',rows);v=dict(config=c,periods={})
        delayed=trades(c,x,mm,delay=1);cent=trades(c,x,mm,precision=.01);five=trades(c,x,mm,quantity=5)
        for co in PERIODS[:4]:
            den=sum(m['asset']==c['asset'] and PERIODS[cohort(m['close'])]==co for m in mm);rr=[r for r in rows if r['cohort']==co]
            pp,rej=portfolio(rr)
            v['periods'][co]=dict(base=metrics(rr,den,bootstrap=True),stress1c=metrics(rr,den,.01),stress2c=metrics(rr,den,.02),minute_delay=metrics([r for r in delayed if r['cohort']==co],den),cent_rounding=metrics([r for r in cent if r['cohort']==co],den),portfolio=metrics(pp,den),portfolio_rejections=rej,five_quote_only=metrics([r for r in five if r['cohort']==co],den))
        out[str(cid)]=v
    save('results.json',out)
    for cid,v in out.items():print(cid,v['config'],[(co,v['periods'][co]['base']['trades'],v['periods'][co]['base']['net']) for co in PERIODS[:4]],flush=True)

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='select':select()
    elif sys.argv[1]=='evaluate':evaluate()
