"""Fixed sell limits at the first z/time trigger, no quote-based signal fitting."""
from pathlib import Path
import sys,json,gzip,pickle
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_timed_z_exits_2026_10_14'))
import grid as g
from search import save
d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=json.loads((P/'configs.json').read_text());ar=dict(np.load(P/'arrays.npz'))
px=np.full(ar['net'].shape,np.nan)
for j,r in enumerate(rr):
 p=g.prepare(r,d,ss);valid=ar['trigger'][:,j]>=0;ix=ar['trigger'][valid,j]-p['t'][0];px[valid,j]=p['low']['price'][ix]
np.savez_compressed(P/'execution_prices.npz',prices=px)
won=np.array([bool(r['won']) for r in rr]);late=np.array([r['close']>=g.SPLIT for r in rr]);hold=np.array([r['hold_net'] for r in rr]);out={};cached={}
for limit in [0,.5,.6,.7,.8,.85,.9,.95]:
 fill=ar['fill']&(px>=limit);net=np.where(fill,ar['net'],hold);delta=net-hold;cached[limit]=(fill,net,delta)
for a in ['ALL','BTC','WTI','NATGAS','USDCAD','FX','FINANCIALS']:
 mask=np.array([a=='ALL' or r['asset']==a or (a=='FX' and r['asset'] in ['EURUSD','USDCAD']) or (a=='FINANCIALS' and r['asset'] in ['WTI','NATGAS','EURUSD','USDCAD']) for r in rr]);ml=mask&late;me=mask&~late;options={}
 for limit,(fill,net,delta) in cached.items():
  wc=fill[:,mask&won].sum(axis=1);cost=-delta[:,mask&won].sum(axis=1);early=delta[:,me].sum(axis=1);change=delta[:,ml].sum(axis=1)
  def m(k,sel):
   v=delta[k,sel];w=won[sel]
   return dict(trades=int(sel.sum()),wins=int(w.sum()),losses=int((~w).sum()),hold=float(hold[sel].sum()),net=float(net[k,sel].sum()),delta=float(v.sum()),saved=float(v[~w].sum()),losses_reduced=int((v[~w]>1e-8).sum()),cuts=int((fill[k,sel]&w).sum()),winner_cost=float(-v[w].sum()),triggered_winners=int(((ar['trigger'][k,sel]>=0)&w).sum()))
  for costcap in [0,1,2,5,10]:
   ok=np.flatnonzero((cost<=costcap+1e-8)&(early>=-1e-8));k=max(ok,key=lambda k:(change[k],early[k],-cost[k],-k));key='cost'+str(costcap)
   v=dict(index=int(k),config=cc[k],sell_limit=limit,full=m(k,mask),later=m(k,ml),earlier=m(k,me),ranking=[float(change[k]),float(early[k]),float(-cost[k]),-limit,-int(k)])
   if key not in options or v['ranking']>options[key]['ranking']:options[key]=v
  for cutcap in [0,1,2,4,8]:
   ok=np.flatnonzero((wc<=cutcap)&(early>=-1e-8));k=max(ok,key=lambda k:(change[k],early[k],-cost[k],-k));key='cuts'+str(cutcap)
   v=dict(index=int(k),config=cc[k],sell_limit=limit,full=m(k,mask),later=m(k,ml),earlier=m(k,me),ranking=[float(change[k]),float(early[k]),float(-cost[k]),-limit,-int(k)])
   if key not in options or v['ranking']>options[key]['ranking']:options[key]=v
 out[a]=options
save('limit_frontier.json',out)
for a,vs in out.items():
 for b,v in vs.items():print(a,b,v['config']['id'],v['sell_limit'],'late',round(v['later']['delta'],4),'early',round(v['earlier']['delta'],4),'cuts',v['full']['cuts'],'cost',round(v['full']['winner_cost'],4),'save',round(v['later']['saved'],4))
