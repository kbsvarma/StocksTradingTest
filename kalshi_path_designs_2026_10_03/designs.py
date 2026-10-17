"""Causal path-based research signals. No network or order transport."""
import bisect,collections,datetime as dt,hashlib,json,math,pickle,sys
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent;ROOT=P.parent;PRIOR=ROOT/'kalshi_z_time_research_2026_10_03'
sys.path.insert(0,str(PRIOR));import study as s
s.old.FOLDERS=s.FOLDERS
from path_risk import simulate_exact

def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2,allow_nan=False))

def configs():
    cc=[]
    for asset,mask in [('BTC',1),('ETH',2),('BTC_ETH',3)]:
        for floor in [.65,.75,.85]:
            for lo,hi in [(3,10),(4,8)]:
                for z in [1.,1.25,1.5]:
                    for k in [2,3]:
                        for advance in [False,True]:cc.append(dict(family='persistence',assets=asset,mask=mask,floor=floor,lo=lo,hi=hi,z=z,k=k,advance=advance))
                    for oz in [.75,1.25]:cc.append(dict(family='cross_asset',assets=asset,mask=mask,floor=floor,lo=lo,hi=hi,z=z,other_z=oz))
            for lo,hi in [(3,10),(2,6)]:
                for z in [1.,1.5]:
                    for drop in [.03,.05,.08]:
                        for advance in [False,True]:cc.append(dict(family='pullback',assets=asset,mask=mask,floor=floor,lo=lo,hi=hi,z=z,drop=drop,advance=advance))
            for z in [.75,1.25]:
                for mom in [.75,1.25]:
                    for quote_change in [0.,.02]:cc.append(dict(family='quote_lag',assets=asset,mask=mask,floor=floor,lo=4,hi=10,z=z,mom=mom,quote_change=quote_change))
    for i,c in enumerate(cc):c['id']=i
    return cc

def augment(markets):
    other={(m['asset'],m['close'],f['t']):f for m in markets if m['asset'] in ('BTC','ETH') for f in m['features']}
    for m in markets:
        if m['asset'] not in ('BTC','ETH'):continue
        lookup={f['t']:f for f in m['features']}
        for f in m['features']:
            # Only contiguous same-direction completed observations qualify as history.
            hist=[]
            for k in range(1,6):
                p=lookup.get(f['t']-60*k)
                if p is None or p['side']!=f['side']:break
                hist.append(p)
            p=hist[0] if hist else None
            f['advance']=bool(p and f['side']*(f['spot']/p['spot']-1)>0)
            f['persist2']=min(f['z'],hist[0]['z']) if len(hist)>=1 else -99.
            f['persist3']=min(f['z'],hist[0]['z'],hist[1]['z']) if len(hist)>=2 else -99.
            armed=[h for h in hist if h['z']>=2 and h['ask']>=.85-1e-8]
            f['discount']=max((h['ask']-f['ask'] for h in armed),default=-99.)
            f['arm_t']=max((h['t'] for h in armed),default=0)
            o=other.get(('ETH' if m['asset']=='BTC' else 'BTC',m['close'],f['t']))
            f['other_z']=o['z'] if o and o['side']==f['side'] else -99.
            p3=hist[2] if len(hist)>=3 else None
            f['momentum3']=f['side']*math.log(f['spot']/p3['spot'])/(f['sigma']*math.sqrt(3)) if p3 else -99.
            f['quote_change3']=f['ask']-p3['ask'] if p3 else 99.
    return markets

def gate(c,f,asset):
    if asset not in ('BTC','ETH') or not ((1<<s.ASSETS.index(asset))&c['mask']):return False
    if not c['lo']<=f['rem']<=c['hi'] or f['z']<c['z'] or not c['floor']-1e-8<=f['ask']<.95-1e-8 or f['ask']-f['bid']>.02000001:return False
    if c['family']=='persistence':return f['persist'+str(c['k'])]>=c['z'] and (not c['advance'] or f['advance'])
    if c['family']=='pullback':return f['discount']>=c['drop']-1e-8 and (not c['advance'] or f['advance'])
    if c['family']=='cross_asset':return f['other_z']>=c['other_z']
    return f['momentum3']>=c['mom'] and f['quote_change3']<=c['quote_change']+1e-8

def inputs(markets,scenario='quote',floor=.65,quantity=5,books=None):
    source={'quote':'quote','book':'books25','book10':'books25_delay10'}[scenario]
    rec,a=pickle.loads((PRIOR/(source+'_opportunities.pickle')).read_bytes());rec=list(rec);rows=a[a[:,s.LANE]<2].tolist()
    fs={};rule=s.Rule('path',floor,.95,.75,2,10,.939)
    for m in markets:
        if m['asset'] not in ('BTC','ETH'):continue
        bb=books.get(m['ticker'],[]) if books is not None else None;times=[b[0] for b in bb] if bb is not None else None
        for f in m['features']:
            if not 2<=f['rem']<=10 or f['z']<.75 or not floor-1e-8<=f['ask']<.95-1e-8 or f['ask']-f['bid']>.02000001:continue
            cap=s.tick_cap(rule,f,m['ranges'])
            if cap is None:continue
            if bb is None:
                q=s.old.quote(m['candles'].get(f['t']+60),'o')
                if q is None:continue
                price=q[1] if f['side']==1 else 1-q[0]
                if not floor-1e-8<=price<=cap+1e-8:continue
                paid=s.fee(price,quantity);entered=f['t']+3
            else:
                j=bisect.bisect_left(times,f['t']+(10 if scenario=='book10' else .3));fill=None
                while j<len(bb) and times[j]<=f['t']+25:
                    fill=s.book_fill(bb[j],f['side'],cap,floor,quantity)
                    if fill:break
                    j+=1
                if fill is None:continue
                price,paid=fill;entered=times[j]
            won=int(f['side']==m['result']);settled=max(m['close']+30,m['settled']);oid=len(rec)
            record=dict(ticker=m['ticker'],asset=m['asset'],t=f['t'],entered=entered,close=m['close'],settled=settled,day=s.old.day(m['close']),entry=price,cap=cap,fee=paid,quantity=quantity,won=won,pnl=quantity*(won-price)-paid,pnl_cap=quantity*(won-cap)-s.fee(cap,quantity),z=f['z'],side=f['side'],rem=f['rem'],ask=f['ask'],profile=0)
            rec.append(record);fs[oid]=f
            rows.append([m['mid'],s.ASSETS.index(m['asset']),0,2,entered,settled,m['close'],s.dayid(entered),s.dayid(settled),f['rem'],f['z'],price,cap,won,paid,oid])
    arr=np.array(rows,float);arr=arr[np.lexsort((arr[:,s.AID],arr[:,s.LANE],arr[:,s.ENTER]))]
    return rec,arr,fs

def select(data,c=None,period=None,standalone=False):
    rec,a,fs=data
    if period and period!='all':a=s.subset(a,period)
    if c is None:return a[a[:,s.LANE]<2]
    keep=[(not standalone) if x[s.LANE]<2 else gate(c,fs[int(x[s.OID])],s.ASSETS[int(x[s.AID])]) for x in a]
    return a[np.array(keep,dtype=bool)]

def run(data,c=None,period=None,quantity=5,stress=.01,capcost=False,standalone=False,miss_rate=0.,miss_seed=0):
    rec,_,_=data;a=select(data,c,period,standalone)
    ix=simulate_exact(a,0 if c else -1,.75,1,14,15,0.,stress,capcost,quantity,False,miss_rate,miss_seed);out=[]
    for i in ix:
        ar=a[i];r=dict(rec[int(ar[s.OID])],lane=['current_signal95','premium_z3','third'][int(ar[s.LANE])]);n=quantity if r['lane']=='third' else 5
        price=r['cap'] if capcost else r['entry'];paid=s.fee(price,n) if capcost else r['fee']
        r.update(quantity=n,entry=price,fee=paid,pnl=n*(r['won']-price)-paid,pnl_cap=n*(r['won']-r['cap'])-s.fee(r['cap'],n));out.append(r)
    return sorted(out,key=lambda r:(r['close'],r['asset']))

def compare(rows,base,stress=.01):
    m=s.old.metrics(rows,stress);b=s.old.metrics(base,stress);ids={r['ticker'] for r in rows};bi={r['ticker'] for r in base}
    m.update(extra=len(rows)-len(base),delta_net=round(m.get('net',0)-b.get('net',0),5),new_markets=len(ids-bi),displaced=len(bi-ids),third=s.old.metrics([r for r in rows if r['lane']=='third'],stress))
    return m

def screen():
    cc=configs();save('configurations.json',cc)
    markets=augment(pickle.loads((PRIOR/'features.pickle').read_bytes()));(P/'features.pickle').write_bytes(pickle.dumps(markets,protocol=5))
    cache={floor:inputs(markets,floor=floor) for floor in [.65,.75,.85]};(P/'quote_inputs.pickle').write_bytes(pickle.dumps(cache,protocol=5))
    ranked=[];baseline={}
    for c in cc:
        data=cache[c['floor']];out=dict(config=c,early={});positive=[];frequency=[];strict=[]
        for co in s.PERIODS[:2]:
            for stress in [.01,.02]:
                base=run(data,period=co,stress=stress);rr=run(data,c,co,stress=stress);mm=compare(rr,base,stress);out['early'][co+str(stress)]=mm
                baseline[co+str(stress)]=s.old.metrics(base,stress);positive.append(mm['delta_net'])
                if stress==.01:
                    frequency.append(mm['extra']/len(base));strict.append(mm['third'].get('net',0)>0 and mm['drawdown']<=s.old.metrics(base,stress)['drawdown']+10)
        out['early_gate']=bool(min(positive)>0 and min(frequency)>=.25 and all(strict))
        out['score']=min(positive)+.15*sum(positive);out['min_growth']=min(frequency);ranked.append(out)
    ranked.sort(key=lambda r:(r['early_gate'],r['score']),reverse=True);save('early_ranking.json',ranked);save('baselines.json',baseline)
    chosen=[]
    for family in ['persistence','pullback','cross_asset','quote_lag']:
        pool=[r for r in ranked if r['config']['family']==family];first=pool[0];chosen.append(first)
        second=next(r for r in pool if r['config']['assets']!=first['config']['assets'] or r['config']['floor']!=first['config']['floor']);chosen.append(second)
        eligible=[r for r in pool if all(r['early'][co+'0.01']['delta_net']>0 for co in s.PERIODS[:2])]
        if eligible:chosen.append(max(eligible,key=lambda r:r['min_growth']))
    chosen=list({x['config']['id']:x for x in chosen}.values())
    save('shortlist.json',dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.digest(P/'PLAN.md'),trials=len(cc),early_gate_passes=sum(r['early_gate'] for r in ranked),candidates=chosen))
    print('SCREENED',len(cc),'PASSES',sum(r['early_gate'] for r in ranked),'SHORTLIST',len(chosen),flush=True)
    for r in chosen:print(r['config'],'gate',r['early_gate'],'early',[(k,x['extra'],x['delta_net']) for k,x in r['early'].items()],flush=True)

if __name__=='__main__':screen()
