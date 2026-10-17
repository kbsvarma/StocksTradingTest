"""Apply the frozen historical feature/model transform to Oct 3 public data."""
import json,math,pickle
import numpy as np
import study as s
P=s.P;R=s.ROOT;src=R/'kalshi_path_designs_2026_10_03/oct3_public'
if (P/'input_sources/oct3_public').exists():src=P/'input_sources/oct3_public'
model_path=P/'input_sources/models.json' if (P/'input_sources/models.json').exists() else R/'kalshi_every_window_2026_10_03/models.json'
models=json.loads(model_path.read_text())
bars={a:{} for a in ('BTC','ETH')}
for a in bars:
    for line in (src/f'spot_{a}.jsonl').open():
        for row in json.loads(line)['rows']:bars[a][int(row[0])]=float(row[4])
def logit(p):
    p=min(.999,max(.001,p));return math.log(p/(1-p))
def features(m,rem):
    T=m['close']-rem*60;a=m['asset'];other='ETH' if a=='BTC' else 'BTC';q=s.q(m['candles'].get(T),'c');ref=bars[a].get(m['start']-60)
    if not q or not ref:return None
    vals=[]
    for asset in (a,other):
        pp=[bars[asset].get(t,np.nan) for t in range(T-3660,T,60)];ret=np.diff(np.log(pp));sig=ret.std()
        if not np.isfinite(ret).all() or sig<=0:return None
        vals.append((pp[-1],sig,float(ret[-15:].std()/sig),*[float(ret[-n:].sum()/(sig*math.sqrt(n))) for n in (1,3,5,15,60)]))
    v,o=vals;z=(v[0]/ref-1)/(v[1]*math.sqrt(rem));lm=logit(sum(q)/2);lr=math.log(rem/7);pq=s.q(m['candles'].get(T-60),'c');dp=lm-logit(sum(pq)/2) if pq else 0;ratio=math.log(max(.05,min(20,v[2])))
    x=[1,lm,lm*abs(lm),lm*lr,max(-6,min(6,z)),z*ratio,*[max(-6,min(6,r)) for r in v[3:]],max(-6,min(6,o[4])),dp]
    model=models[a+'_rich'];p=float(1/(1+np.exp(-np.clip(np.array(x)/model['scale']@model['coef'],-30,30))))
    return z,x[6],p,v[1]
mm=sorted([m for m in pickle.loads((src/'features.pickle').read_bytes()) if m['asset'] in bars],key=lambda m:(m['close'],m['asset']))
x=np.full((len(mm),14,10),np.nan);meta=[]
for i,m in enumerate(mm):
    for j,rem in enumerate(range(14,0,-1)):
        T=m['close']-60*rem;c=s.q(m['candles'].get(T),'c');o=s.q(m['candles'].get(T+60),'o');d=s.q(m['candles'].get(T+60),'c') if rem>1 else None
        if c:x[i,j,:2]=c
        if o:x[i,j,2:4]=o
        if d:x[i,j,4:6]=d
        f=features(m,rem)
        if f:x[i,j,6:]=f
    meta.append({k:m[k] for k in ('ticker','asset','start','close','settled','result')})
np.save(P/'oct3_arrays.npy',x);s.save('oct3_markets.json',meta)
frozen=json.loads((P/'frozen_selection.json').read_text());cc={z['config']['id']:z['config'] for z in frozen['selected'] if z['config']};cc.update({c['id']:c for c in frozen['references']})
out={}
for cid,c in cc.items():
    rows=s.trades(c,x,meta);s.save(f'oct3_quote_trades_{cid}.json',rows);out[cid]=s.metrics(rows,sum(m['asset']==c['asset'] for m in meta))
s.save('oct3_quote_results.json',out);print('Oct3 markets',len(mm),'from existing previously examined pull; not untouched')
