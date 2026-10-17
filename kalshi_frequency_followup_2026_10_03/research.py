"""Public-data-only follow-up; no live client, credentials or order code."""
import sys,inspect
from pathlib import Path
P=Path(__file__).resolve().parent;PRIOR=P.parent/'kalshi_z_time_research_2026_10_03'
sys.path.insert(0,str(PRIOR))
import study as s
import numpy as np
import json,pickle,bisect,math,datetime as dt
sys.path.insert(0,str(P))
from risk import simulate_protected

def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2,allow_nan=False))

def inputs(scenario,cap,markets,books):
    source={'quote':'quote','book':'books25','book10':'books25_delay10'}[scenario]
    rec,a=pickle.loads((PRIOR/(source+'_opportunities.pickle')).read_bytes());rec=list(rec)
    rows=a[a[:,s.LANE]<2].tolist();rule=s.Rule('candidate',.85,.95,.75,3,10,cap)
    for m in markets:
        if m['asset'] not in ('BTC','ETH'):continue
        bb=books.get(m['ticker'],[]) if scenario!='quote' else None;times=[x[0] for x in bb] if bb is not None else None
        for f in m['features']:
            if not 3<=f['rem']<=10 or f['z']<.75 or not .85-1e-8<=f['ask']<.95-1e-8 or f['ask']-f['bid']>.02000001:continue
            capprice=s.tick_cap(rule,f,m['ranges'])
            if capprice is None:continue
            if bb is None:
                q=s.old.quote(m['candles'].get(f['t']+60),'o')
                if q is None:continue
                entry=q[1] if f['side']==1 else 1-q[0]
                if not .85-1e-8<=entry<=capprice+1e-8:continue
                paid=s.fee(entry,5);entered=f['t']+3
            else:
                j=bisect.bisect_left(times,f['t']+(10 if scenario=='book10' else .3));fill=None
                while j<len(bb) and times[j]<=f['t']+25:
                    fill=s.book_fill(bb[j],f['side'],capprice,.85,5)
                    if fill:break
                    j+=1
                if not fill:continue
                entry,paid=fill;entered=times[j]
            won=int(f['side']==m['result']);settled=max(m['close']+30,m['settled']);oid=len(rec)
            rec.append(dict(ticker=m['ticker'],asset=m['asset'],t=f['t'],entered=entered,close=m['close'],settled=settled,day=s.old.day(m['close']),entry=entry,cap=capprice,fee=paid,quantity=5,won=won,pnl=5*(won-entry)-paid,pnl_cap=5*(won-capprice)-s.fee(capprice,5),z=f['z'],side=f['side'],rem=f['rem'],ask=f['ask']))
            rows.append([m['mid'],s.ASSETS.index(m['asset']),0,2,entered,settled,m['close'],s.dayid(entered),s.dayid(settled),f['rem'],f['z'],entry,capprice,won,paid,oid])
    arr=np.array(rows,float);arr=arr[np.lexsort((arr[:,s.AID],arr[:,s.LANE],arr[:,s.ENTER]))]
    return rec,arr

def run(rec,a,c=None,stress=.01,capcost=False,miss_rate=0,miss_seed=0):
    args=[0,c['z'],c['lo'],c['hi'],c['mask'],0] if c else [-1,99,1,14,15,0]
    ix=simulate_protected(a,*args,stress,capcost,5,False,miss_rate,miss_seed,c['buffer'] if c else 0);out=[]
    for i in ix:
        r=dict(rec[int(a[i,s.OID])],lane=['current_signal95','premium_z3','third'][int(a[i,s.LANE])])
        if capcost:r.update(entry=r['cap'],fee=s.fee(r['cap'],5),pnl=r['pnl_cap'])
        out.append(r)
    return sorted(out,key=lambda r:(r['close'],r['asset']))

def compare(rr,base,stress=.01):
    m=s.old.metrics(rr,stress);b=s.old.metrics(base,stress);bm={r['ticker'] for r in base};jm={r['ticker'] for r in rr}
    m.update(delta_net=round(m['net']-b['net'],5),extra=len(rr)-len(base),new_markets=len(jm-bm),lost_baseline_markets=len(bm-jm),third=s.old.metrics([r for r in rr if r['lane']=='third'],stress));return m

def main():
    markets=pickle.loads((PRIOR/'features.pickle').read_bytes());s.old.FOLDERS=s.FOLDERS;books=s.old.load_books();cache={};configs=[]
    for cap in [.91,.92,.93,.939]:
        for scenario in ['quote','book','book10']:
            cache[scenario,cap]=inputs(scenario,cap,markets,books)
        for asset,mask in [('BTC',1),('ETH',2),('BTC_ETH',3)]:
            for z in [.75,1.,1.25,1.5,1.75,2.]:
                for lo,hi in [(3,10),(4,8),(5,7),(6,7)]:
                    for buffer in [0,5]:configs.append(dict(id=len(configs),cap=cap,assets=asset,mask=mask,z=z,lo=lo,hi=hi,buffer=buffer))
    save('configurations.json',configs);(P/'inputs.pickle').write_bytes(pickle.dumps(cache,protocol=5));print('INPUTS READY',len(configs),flush=True)
    baselines={};rows=[]
    for c in configs:
        out=dict(config=c,early={});positive=[];growth=[]
        for scenario,stress,label in [('quote',.01,'quote1c'),('quote',.02,'quote2c'),('book',.01,'book1c'),('book10',.01,'book10')]:
            rec,a=cache[scenario,c['cap']]
            for co in s.PERIODS[:2]:
                aa=s.subset(a,co);base=run(rec,aa,stress=stress);rr=run(rec,aa,c,stress=stress);m=compare(rr,base,stress)
                out['early'][label+'_'+co]=m;baselines[label+'_'+co]=s.old.metrics(base,stress)
                positive.append(m['delta_net'])
                if label=='quote1c':growth.append(m['extra']/len(base))
        out['robust']=bool(min(positive)>0 and min(growth)>=.3)
        out['score']=min(positive)+.1*sum(positive)
        rows.append(out)
    rows.sort(key=lambda r:(r['robust'],r['score']),reverse=True);save('early_ranking.json',rows);save('baselines.json',baselines)
    chosen=[];seen=set()
    for r in rows:
        c=r['config'];key=(c['cap'],c['assets'],c['buffer'])
        if key in seen:continue
        seen.add(key);chosen.append(r)
    # All 24 asset/cap/buffer family winners, selected without later results.
    for r in rows:
        c=r['config']
        if c['assets']=='BTC_ETH' and c['z'] in [1.25,1.5] and (c['lo'],c['hi'])==(6,7) and c['cap']==.939 and c['buffer']==0 and r not in chosen:chosen.append(r)
    save('frozen_selection.json',dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.digest(P/'PLAN.md'),trials=len(configs),robust_early=sum(r['robust'] for r in rows),candidates=chosen))
    print('EARLY ROBUST',sum(r['robust'] for r in rows),'FROZEN',len(chosen),flush=True)
    out={};saved={}
    for item in chosen:
        c=item['config'];cid=str(c['id']);rec,a=cache['quote',c['cap']];out[cid]={};saved[cid]={}
        for co in s.PERIODS[2:]:
            aa=s.subset(a,co);base=run(rec,aa);rr=run(rec,aa,c);result=compare(rr,base);saved[cid][co]=rr
            result['stress2c']=compare(run(rec,aa,c,stress=.02),run(rec,aa,stress=.02),.02)
            result['cap_plus1c']=compare(run(rec,aa,c,capcost=True),run(rec,aa,capcost=True))
            result['baseline']=s.old.metrics(base);out[cid][co]=result
        print('RESULT',c,item['robust'],{k:out[cid]['later'][k] for k in ['extra','delta_net','drawdown']},'2c',out[cid]['later']['stress2c']['delta_net'],'oct2',out[cid]['october2']['delta_net'],flush=True)
    save('results.json',out);(P/'selected_trades.pickle').write_bytes(pickle.dumps(saved,protocol=5))
    # Zero-buffer, same-cap regression against the previous model.
    rec,a=cache['quote',.939];aa=s.subset(a,'later');base=run(rec,aa);assert len(base)==302 and s.old.metrics(base)['net']==65.9172
    c=next(c for c in configs if c['cap']==.939 and c['assets']=='BTC_ETH' and c['z']==1.25 and c['lo']==6 and c['hi']==7 and c['buffer']==0)
    rr=run(rec,aa,c);assert len(rr)==689 and s.old.metrics(rr)['net']==84.938
    save('regression.json',dict(baseline='302 / +65.9172 exact',prior_high_frequency='689 / +84.938 exact',quote_proxy=True,real_orders=False));print('PASS',flush=True)

if __name__=='__main__':main()
