"""Isolated targeted screen: no live imports, writes or financial calls."""
from pathlib import Path
import sys,json,gzip,pickle,math,datetime
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_timed_z_exits_2026_10_14'))
import grid as g
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def configs():
 out=[dict(id='hold',family='hold',level=0,grace=0,persist=0,lo=0,hi=900)]
 windows=[(0,900),(0,300),(0,180),(0,120),(0,90),(0,60),(10,120),(30,180),(60,300),(90,900),(60,900),(30,900),(10,900)]
 for family,levels in [('absolute',[-1,-.75,-.5,-.25,0,.25,.5,.75,1,1.25,1.5,1.75,2,2.25,2.5,3]),('entry_distance',[.1,.25,.5,.75]),('peak_distance',[.1,.25,.5,.75])]:
  for z in levels:
   for grace in [0,15,30,60]:
    for persist in [0,2,5,10,20,30,60]:
     for lo,hi in windows:
      out.append(dict(id=f'{family}_{z:g}_g{grace}_p{persist}_r{lo}-{hi}',family=family,level=z,grace=grace,persist=persist,lo=lo,hi=hi))
 # Abrupt deterioration: current absolute z plus drop over an observed lookback.
 for level in [.5,1,1.5,2]:
  for lookback in [5,10,30,60]:
   for drop in [.25,.5,1]:
    for persist in [0,2,5]:
     for lo,hi in [(0,900),(10,900),(30,900),(0,180)]:
      out.append(dict(id=f'velocity_z{level:g}_d{drop:g}_l{lookback}_p{persist}_r{lo}-{hi}',family='velocity',level=level,grace=0,persist=persist,lo=lo,hi=hi,lookback=lookback,drop=drop))
 return out
def firsts(r,p,cc):
 z=p['z'];rem=p['remaining'];age=p['age'];distance=z*np.sqrt(rem/60);entrydistance=r['entry_z']*math.sqrt((r['close']-r['t'])/60)
 peak=np.maximum.accumulate(np.maximum(distance,entrydistance));breach=[]
 for c in cc:
  f=c['family'];lev=c['level'];test=z<lev
  if f=='hold':test=np.zeros(len(z),bool)
  if f=='entry_distance':test=distance<lev*entrydistance
  if f=='peak_distance':test=distance<lev*peak
  if f=='velocity':
   lb=c['lookback'];lag=np.full(len(z),np.nan);lag[lb:]=z[:-lb];test&=(lag-z>=c['drop'])
  breach.append(test&(age>=c['grace'])&(rem>c['lo'])&(rem<=c['hi']))
 b=np.array(breach);ix=np.arange(len(z),dtype=np.int16)[None,:];elapsed=ix-np.maximum.accumulate(np.where(~b,ix,-1),axis=1)-1
 qual=b&(elapsed>=np.array([c['persist'] for c in cc])[:,None]);out=qual.argmax(axis=1);out[~qual.any(axis=1)]=-1
 return out
def main():
 d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=configs();save('configs.json',cc)
 K,N=len(cc),len(rr);print('configs',K,'trades',N,flush=True)
 arrays=dict(net=np.empty((K,N)),fill=np.zeros((K,N),bool),trigger=np.full((K,N),-1,np.int64))
 for j,r in enumerate(rr):
  p=g.prepare(r,d,ss);first=np.concatenate([firsts(r,p,cc[k:k+1000]) for k in range(0,K,1000)]);ok=first>=0
  arrays['net'][:,j]=r['hold_net'];arrays['net'][ok,j]=p['low']['net'][first[ok]];arrays['fill'][ok,j]=p['low']['fill'][first[ok]];arrays['trigger'][ok,j]=p['t'][first[ok]]
  if j%50==0:print('paths',j,flush=True)
 np.savez_compressed(P/'arrays.npz',**arrays)
 # All selection below is explicitly latest-period hindsight, not validation.
 won=np.array([bool(r['won']) for r in rr]);late=np.array([r['close']>=g.SPLIT for r in rr]);hold=np.array([r['hold_net'] for r in rr]);delta=arrays['net']-hold
 frontier={}
 for a in ['ALL','BTC','WTI','NATGAS','USDCAD','FX','FINANCIALS']:
  mask=np.array([a=='ALL' or r['asset']==a or (a=='FX' and r['asset'] in ['EURUSD','USDCAD']) or (a=='FINANCIALS' and r['asset'] in ['WTI','NATGAS','EURUSD','USDCAD']) for r in rr]);ml=mask&late;me=mask&~late
  wc=arrays['fill'][:,mask&won].sum(axis=1);cost=-delta[:,mask&won].sum(axis=1);early=delta[:,me].sum(axis=1);change=delta[:,ml].sum(axis=1);saved=delta[:,ml&~won].sum(axis=1)
  def m(k,mask):
   v=delta[k,mask];w=won[mask];fl=arrays['fill'][k,mask]
   return dict(trades=int(mask.sum()),wins=int(w.sum()),losses=int((~w).sum()),hold=float(hold[mask].sum()),net=float(arrays['net'][k,mask].sum()),delta=float(v.sum()),saved=float(v[~w].sum()),losses_reduced=int((v[~w]>1e-8).sum()),cuts=int((fl&w).sum()),winner_cost=float(-v[w].sum()),triggered_winners=int(((arrays['trigger'][k,mask]>=0)&w).sum()))
  frontier[a]={}
  for budget in [0,1,2,4,8,16]:
   ok=np.flatnonzero(wc<=budget);k=max(ok,key=lambda k:(change[k],early[k],-wc[k],-k))
   frontier[a]['cuts'+str(budget)]=dict(index=int(k),config=cc[k],full=m(k,mask),later=m(k,ml),earlier=m(k,me))
  for costcap in [0,1,2,5,10]:
   ok=np.flatnonzero((cost<=costcap+1e-8)&(early>=-1e-8));k=max(ok,key=lambda k:(change[k],early[k],-cost[k],-k))
   frontier[a]['cost'+str(costcap)]=dict(index=int(k),config=cc[k],full=m(k,mask),later=m(k,ml),earlier=m(k,me))
 save('frontier.json',frontier)
 for a,vs in frontier.items():
  for b,v in vs.items():print(a,b,v['config']['id'],'late',round(v['later']['delta'],4),'early',round(v['earlier']['delta'],4),'cuts',v['full']['cuts'],'cost',round(v['full']['winner_cost'],4),'save',round(v['later']['saved'],4),flush=True)
if __name__=='__main__':main()
