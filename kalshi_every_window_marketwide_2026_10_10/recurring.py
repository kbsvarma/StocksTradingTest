"""Mandatory fixed-horizon decisions across recurring binary market families."""
from core import *
import statistics,pickle,io,hashlib,bisect
ROOT=P.parent
POLICIES=['favorite','underdog','model','blend','available_favorite','available_model']
class SafeUnpickler(pickle.Unpickler):
    def find_class(self,module,name):raise ValueError('Nonprimitive pickle: '+module+'.'+name)
def primitive(path):return SafeUnpickler(io.BytesIO(path.read_bytes())).load()
def normal(z):return .5*(1+math.erf(z/math.sqrt(2)))
def normalize_bars(cm):return {k:{int(c['end_period_ts']):c for c in v} for k,v in cm.items()}
def vol(cs,t):return sum(float(cs.get(u,{}).get('volume_fp') or cs.get(u,{}).get('volume') or 0) for u in range(t-540,t+60,60))
def select(options,policy):
    if policy.startswith('available_'):
        options=[x for x in options if .01<=x['ask']<=.99 and x['ask']-x['bid']<=.05000001 and vol(x['_cs'],x['_t'])>=5]
        if policy=='available_favorite':
            favorites=[x for x in options if x['mid']>=.5]
            return min(favorites,key=lambda x:(abs(x['mid']-.5),x['ticker'],x['side'])) if favorites else None
        policy=policy.removeprefix('available_')
    if not options:return None
    if policy in ('favorite','underdog'):
        yes=[x for x in options if x['side']=='yes']
        if not yes:return None
        atm=min(yes,key=lambda x:(abs(x['mid']-.5),x['ticker']))
        fav='yes' if atm['mid']>=.5 else 'no';side=fav if policy=='favorite' else ('no' if fav=='yes' else 'yes')
        return next(x for x in options if x['ticker']==atm['ticker'] and x['side']==side)
    return max(options,key=lambda x:((x['prob'] if policy=='model' else .5*(x['prob']+x['mid']))-x['ask']-fee(x['ask'],1,x['mult']),-abs(x['mid']-.5),x['ticker'],x['side']))
def execute(x,t,mode):
    if not .01<=x['ask']<=.99 or x['ask']-x['bid']>.05000001:return None,'price_or_spread'
    cs=x['_cs']
    if vol(cs,t)<5:return None,'volume'
    q=qte(cs.get(t+60),x['side'],'open' if mode=='base' else 'close')
    if not q:return None,'execution_quote'
    entry=max(x['ask'],q[1]);cap=min(.99,x['ask']+.02);entered=t+(3 if mode=='base' else 60);m=x['_m']
    if entry>cap+1e-8:return None,'execution_cap'
    if entered>=min(x['close'],ts(m['settlement_ts'])):return None,'after_close'
    return {**{k:v for k,v in x.items() if not k.startswith('_')},'t':t,'entered':entered,'entry':entry,'cap':cap,'settled':ts(m['settlement_ts']),'payout':pay(m,x['side']),'volume10':vol(cs,t)},None
def choices(items,cm,t,asset,owner,kind,mult,probs,extras=None):
    out=[]
    for m in items:
        cs=cm.get(m['ticker'],{});py=probs.get(m['ticker'])
        if py is None or not m.get('settlement_ts'):continue
        for side in ['yes','no']:
            q=qte(cs.get(t),side)
            if q:
                b,a=q;out.append(dict(asset=asset,kind=kind,owner=owner,event=m['event_ticker'],ticker=m['ticker'],side=side,ask=a,bid=b,mid=(a+b)/2,prob=py if side=='yes' else 1-py,mult=mult,close=ts(m['close_time']),_m=m,_cs=cs,_t=t,**(extras or {})))
    return out

def hourly():
    base=ROOT/'kalshi_noncrypto_consistency_2026_10_10/financial'
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not (d/'markets.json').exists():continue
        groups=defaultdict(list)
        for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
        mult=float(read(d/'series.json')['fee_multiplier'])
        for et,items in groups.items():
            cl=ts(items[0]['close_time'])
            # This fixed daily commodity expiry was selected before prior studies.
            if d.name in ('KXGOLDH','KXSILVERH','KXWTIH','KXPALLADIUMH') and datetime.fromtimestamp(cl,ET).hour!=16:continue
            w={'owner':et,'asset':d.name,'kind':'hourly','date':day(cl),'close':cl,'split':'2026-09-05'}
            cp=d/(et+'_chart.json.gz');qp=d/(et+'_candles.json.gz')
            if not cp.exists() or not qp.exists():yield w,{};continue
            details=read(cp).get('live_data',{}).get('details',{});raw=details.get('timeseries',[]);fine=any(int(x['t'])%60000 for x in raw);bars={}
            for x in raw:
                tm=int(x['t']);v=float(x['v'])
                if (tm%60000==59000 if fine else tm%60000==0) and math.isfinite(v) and v>0:bars[tm//60000*60]=v
            cm=normalize_bars(read(qp));points={}
            for h in [30,15,5]:
                t=cl-h*60;keys=list(range(t-3660,t,60));good=all(k in bars for k in keys);probs={};sigma=None
                if good:
                    ps=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(b/a) for a,b in zip(ps,ps[1:]))
                for m in items:
                    q=qte(cm.get(m['ticker'],{}).get(t))
                    if not q:continue
                    strike=m.get('floor_strike')
                    if m.get('strike_type') not in ('greater','greater_or_equal') or not strike:continue
                    probs[m['ticker']]=normal((bars[t-60]/float(strike)-1)/(1.25*sigma*math.sqrt(h))) if sigma else sum(q)/2
                points[h]=(t,choices(items,cm,t,d.name,et,'hourly',mult,probs,{'source_last':t-1 if fine else t-60,'model_fallback':not bool(sigma),'horizon':h}))
            yield w,points

def fifteen():
    paths=[ROOT/'kalshi_noncrypto15m_2026_10_07',ROOT/'kalshi_broad_review_2026_10_10/extension']
    seen=set();receipts=[]
    for d in reversed(paths):
        file=d/'features.pickle'
        if not file.exists():continue
        markets=primitive(file);bars=primitive(d/'underlying_minutes.pickle');receipts.append({'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        for old in markets:
            if old['ticker'] in seen:continue
            seen.add(old['ticker']);asset=old['asset'];cl=old['close'];start=old['start'];et=old['ticker'];w={'owner':et,'asset':'15M_'+asset,'kind':'15minute','date':day(cl),'close':cl,'split':'2026-10-01'}
            m={'ticker':et,'event_ticker':et,'close_time':datetime.fromtimestamp(cl,ZoneInfo('UTC')).isoformat(),'settlement_ts':datetime.fromtimestamp(old['settled'],ZoneInfo('UTC')).isoformat(),'result':'yes' if old['result']==1 else 'no'}
            cs={}
            for stamp,c in old['candles'].items():
                cs[int(stamp)]={'end_period_ts':int(stamp),'volume':c.get('v',0),'yes_bid':{'open_dollars':c.get('bo'),'close_dollars':c.get('bc')},'yes_ask':{'open_dollars':c.get('ao'),'close_dollars':c.get('ac')}}
            points={};b=bars.get(asset,{})
            for h in [10,7,3]:
                t=cl-h*60;keys=list(range(t-3660,t,60));py=None;good=all(k in b for k in keys) and start-60 in b
                if good:
                    ps=[b[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(ps,ps[1:]))
                    if sigma:py=normal((b[t-60]/b[start-60]-1)/(1.25*sigma*math.sqrt(h)))
                q=qte(cs.get(t))
                if py is None and q:py=sum(q)/2
                points[h]=(t,choices([m],{et:cs},t,'15M_'+asset,et,'15minute',old['multiplier'],{et:py}, {'source_last':t-1,'model_fallback':not good,'horizon':h}))
            yield w,points
    save(P/'primitive_input_receipts.json',receipts)

def weather():
    base=ROOT/'kalshi_weather_validation_2026_10_10/data';anchor=ts('2026-08-15T04:00:00Z');week=7*86400
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not (d/'bars.json.gz').exists():continue
        bars={int(t):v for t,v in read(d/'bars.json.gz').items()};groups=defaultdict(list)
        for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
        mult=float(read(d/'series.json')['fee_multiplier']);events=[]
        for et,ms in groups.items():
            cl=ts(ms[0]['close_time'])
            if any(not m.get('settlement_ts') or not m.get('expiration_value') for m in ms):
                if '2026-09-19'<=day(cl)<='2026-10-09':yield {'owner':et,'asset':d.name,'kind':'weather','date':day(cl),'close':cl,'split':'2026-10-03','missing':'settlement_metadata'},{}
                continue
            final=float(ms[0]['expiration_value']);settled=max(ts(m['settlement_ts']) for m in ms);events.append((et,ms,cl,settled,final))
        errors={}
        def prediction(t):
            last=t-600;keys=range(last-3600,last+1,60)
            return bars[last][0] if all(k in bars for k in keys) and bars[last][1]=='normal' else None
        for et,items,cl,settled,final in sorted(events,key=lambda x:x[2]):
            if not '2026-09-19'<=day(cl)<='2026-10-09':continue
            w={'owner':et,'asset':d.name,'kind':'weather','date':day(cl),'close':cl,'split':'2026-10-03'};cp=d/(et+'_candles.json.gz');cm=normalize_bars(read(cp)) if cp.exists() else {};points={}
            for h in [30,15,5]:
                t=cl-h*60;boundary=anchor+(t-anchor)//week*week;key=(boundary,h)
                if key not in errors:
                    errors[key]=[f-v for _,_,c,s,f in events if boundary-week<=c<boundary and s<boundary and (v:=prediction(c-h*60)) is not None]
                ee=errors[key];pred=prediction(t);probs={};fallback=pred is None or len(ee)<60
                for m in items:
                    q=qte(cm.get(m['ticker'],{}).get(t))
                    if q:probs[m['ticker']]=sum(q)/2 if fallback else (sum(pred+x>float(m['floor_strike']) for x in ee)+1)/(len(ee)+2)
                points[h]=(t,choices(items,cm,t,d.name,et,'weather',mult,probs,{'source_last':t-600,'model_fallback':fallback,'horizon':h,'calibration_end':boundary,'calibration_n':len(ee)}))
            yield w,points

def main():
    import sys
    names=sys.argv[1:] or ['hourly','fifteen','weather'];cand=defaultdict(list);windows=[];skips=defaultdict(Counter)
    for name in names:
        for n,(w,points) in enumerate(globals()[name](),1):
            windows.append(w)
            for h in ([10,7,3] if name=='fifteen' else [30,15,5]):
                for policy in POLICIES:
                    for mode in ['base','delay60']:
                        key=w['asset']+'/m'+str(h)+'/'+policy+'/'+mode
                        if h not in points:skips[key]['missing_inputs']+=1;continue
                        t,options=points[h];pick=select(options,policy)
                        if pick is None:skips[key]['missing_quote']+=1;continue
                        row,reason=execute(pick,t,mode)
                        if row:row.update(date=w['date'],split=w['split']);cand[key].append(row)
                        else:skips[key][reason]+=1
            if n%300==0:print('BUILT',name,n,flush=True)
    prefix='_'.join(names);save(P/(prefix+'_candidates.json.gz'),cand);save(P/(prefix+'_windows.json'),windows);save(P/(prefix+'_skips.json'),{k:dict(v) for k,v in skips.items()})
    results={};ledgers={}
    for key in sorted(set(cand)|set(skips)):
        rows=cand.get(key,[])
        asset=key.split('/')[0];ws=[w for w in windows if w['asset']==asset];split=ws[0]['split']
        for part in ['all','early','late']:
            pick=lambda d:part=='all' or (d<split if part=='early' else d>=split)
            rr=[r for r in rows if pick(r['date'])];den=sum(pick(w['date']) for w in ws)
            for qty in [1,5]:
                for stress in [.01,.02]:record(results,ledgers,key+'/'+part+f'/q{qty}/cost{round(stress*100)}',rr,den,qty,stress)
    save(P/(prefix+'_results.json'),results);save(P/(prefix+'_ledgers.json.gz'),ledgers)
    best=[(k,v) for k,v in results.items() if '/base/all/q1/cost1' in k]
    for k,v in sorted(best,key=lambda kv:kv[1]['net'],reverse=True)[:20]:print('RESULT',k,{z:v[z] for z in ['windows','trades','wins','losses','net','participation','drawdown']},flush=True)
if __name__=='__main__':main()
