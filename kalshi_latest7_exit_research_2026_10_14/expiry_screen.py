"""Test proactive late cashout as well as deterioration, all actual trades."""
from pathlib import Path
import sys,json,gzip,pickle
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_timed_z_exits_2026_10_14'))
import grid as g
from search import firsts,save
d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=[]
for z in [2,3,4,5,8,1e9]:
 for hi in [300,180,120,90,65,62,61,60,30]:
  for grace in [0,60]:
   for persist in [0,5,15,30]:
    for limit in [0,.85,.9,.95,.97,.98]:cc.append(dict(id=f'expiry_z{z:g}_r{hi}_g{grace}_p{persist}_floor{limit}',family='absolute',level=z,hi=hi,lo=0,grace=grace,persist=persist,limit=limit))
net=np.empty((len(cc),len(rr)));fill=np.zeros(net.shape,bool);trigger=np.full(net.shape,-1,np.int64)
for j,r in enumerate(rr):
 p=g.prepare(r,d,ss);first=firsts(r,p,cc);ok=first>=0;fi=np.maximum(first,0);ff=ok&p['low']['fill'][fi]&(p['low']['price'][fi]>=np.array([c['limit'] for c in cc]));net[:,j]=np.where(ff,p['low']['net'][fi],r['hold_net']);fill[:,j]=ff;trigger[ok,j]=p['t'][fi[ok]]
np.savez_compressed(P/'expiry_arrays.npz',net=net,fill=fill,trigger=trigger);save('expiry_configs.json',cc)
won=np.array([bool(r['won']) for r in rr]);late=np.array([r['close']>=g.SPLIT for r in rr]);hold=np.array([r['hold_net'] for r in rr]);delta=net-hold;result={}
for a in ['ALL','BTC','WTI','NATGAS','FX','FINANCIALS']:
 mask=np.array([a=='ALL' or r['asset']==a or (a=='FX' and r['asset'] in ['EURUSD','USDCAD']) or (a=='FINANCIALS' and r['asset'] in ['WTI','NATGAS','EURUSD','USDCAD']) for r in rr]);ml=mask&late;me=mask&~late;cost=-delta[:,mask&won].sum(axis=1);cuts=fill[:,mask&won].sum(axis=1);early=delta[:,me].sum(axis=1);change=delta[:,ml].sum(axis=1);total=delta[:,mask].sum(axis=1)
 def m(k,m):
  v=delta[k,m];w=won[m];return dict(net=float(net[k,m].sum()),delta=float(v.sum()),saved=float(v[~w].sum()),cuts=int((fill[k,m]&w).sum()),winner_cost=float(-v[w].sum()),losses_reduced=int((v[~w]>1e-8).sum()))
 result[a]={}
 for budget in [0,1,2,5,10,1000]:
  ok=np.flatnonzero((cost<=budget+1e-9)&(total>=0)&(early>=-2))
  if not len(ok):continue
  k=max(ok,key=lambda k:(change[k],total[k],-cuts[k],-k));result[a][str(budget)]=dict(config=cc[k],index=int(k),full=m(k,mask),later=m(k,ml),earlier=m(k,me))
save('expiry_frontier.json',result)
for a,vs in result.items():
 for b,v in vs.items():print(a,b,v['config']['id'],v['full'],v['later'],v['earlier'],flush=True)
