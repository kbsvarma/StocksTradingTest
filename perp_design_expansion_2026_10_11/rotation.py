from slow import *
from numba import njit
@njit
def rotate(A,F,S,start,end,rates,fr,delay,adverse,hold):
 out=np.zeros((end-start+1,16));n=0;cash=100.;peak=100.;dd=0.;pos=0;pending=0;due=0;asset=0;side=0;ep=0.;ei=0;qty=0;stop=0.;expiry=0;used=-1;reason=0;last=100.;missing=0;path=np.zeros(end-start)
 for i in range(start,end):
  if pending==1 and i>=due:
   q=A[asset,i];valid=np.isfinite(q[0]+q[1])
   if valid and (q[1]-q[0])/q[:2].mean()<=.005:
    ep=q[1] if side>0 else q[0];qty=int(min(.5*cash/ep,min(2.,.02*cash)/(ep*(stop+2*(rates[asset]+fr)+(q[1]-q[0])/ep))))
    if qty>0:pos=1;ei=i
   pending=0
  if pos:
   q=A[asset,i];valid=np.isfinite(q[0]+q[1])
   if valid:
    px=q[0] if side>0 else q[1];g=side*qty*(px-ep);ff=-side*qty*(F[asset,i]-F[asset,ei]);fees=qty*(ep+px)*rates[asset];slip=qty*(ep+px)*fr;net=g+ff-fees-slip
    if (pending==2 and i>=due) or i==end-1:
     if adverse and reason==1:
      price=min(px,ep*(1-stop)) if side>0 else max(px,ep*(1+stop));penalty=side*qty*(px-price);slip+=max(0.,penalty);net=g+ff-fees-slip
     out[n]=np.array([asset,ei,i,side,qty,ep,px,g,ff,fees,slip,net,reason,stop,expiry,used]);n+=1;cash+=net;pos=0;pending=0
    elif pending==0:
     bad=q[2] if side>0 else q[3]
     if side*(px/ep-1)<=-stop or (i>ei and np.isfinite(bad) and side*(bad/ep-1)<=-stop):pending=2;due=i+delay;reason=1
     elif i>=expiry-1 or i>=end-61:pending=2;due=i+delay;reason=3
    last=cash+net if pos else cash
   else:missing+=1
  else:last=cash
  peak=max(peak,last);dd=max(dd,peak-last);path[i-start]=last
  if not pos and pending==0 and S[i,0]>=0 and S[i,3]>used and i<end-61-delay:
   asset=int(S[i,0]);side=S[i,1];stop=S[i,2];used=int(S[i,3]);expiry=used+hold;pending=1;due=i+delay
 return out[:n],path,dd,missing

def build():
 tickers=[c['ticker'] for c in json.load(open(P/'coverage.json')) if r.old.M[c['ticker']]['asset_class']=='Crypto']+['KXETHPERP','KXXRPPERP','KXBNBPERP','KXHYPEPERP','KXZECPERP'];cache={t:data_load(t)[:3] for t in tickers};start=min(x[0][0] for x in cache.values());end=1791590400;ts=np.arange(start,end,60);A=np.full((len(tickers),len(ts),7),np.nan);F=np.zeros((len(tickers),len(ts)));vol=[];scores={h:[] for h in [12,24,72]}
 for j,t in enumerate(tickers):
  tt,aa,ff=cache[t];sel=(tt>=start)&(tt<end);ix=np.searchsorted(ts,tt[sel]);A[j,ix]=aa[sel];F[j]=np.interp(ts,tt,ff,left=ff[0],right=ff[-1]);q=pd.Series(A[j,:,:2].mean(axis=1),index=ts);q=q[q.index%3600==0];lp=np.log(q);v=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);vol.append(v.reindex(ts).to_numpy())
  for h in scores:scores[h].append(((lp-lp.shift(h))/v).reindex(ts).to_numpy())
 return tickers,ts,A,F,np.array(vol),{h:np.array(v) for h,v in scores.items()}
if __name__=='__main__':
 tickers,ts,A,F,vol,scores=build();results=[];(P/'rotation_ledgers').mkdir(exist_ok=True);rates=np.array([r.fees['taker_fee_rates'][t] for t in tickers]);(P/'rotation_universe.json').write_text(json.dumps(tickers))
 for h in [12,24,72]:
  for holdh in [8,24]:
   for sm in [1,2]:
    for longonly in [0,1]:
     S=np.zeros((len(ts),4));S[:,0]=-1
     for i in np.flatnonzero(ts%(holdh*3600)==0):
      sc=scores[h][:,i];vv=vol[:,i];stops=np.maximum(.005,sm*vv);rank=sc.copy() if longonly else np.abs(sc);rank[(~np.isfinite(rank+stops))|(stops>.12)]=-np.inf;j=int(np.argmax(rank))
      if rank[j]<1:continue
      S[i:min(i+10,len(ts))]=[j,np.sign(sc[j]),stops[j],i]
     for period in ['early','later']:
      split=np.searchsorted(ts,int(pd.Timestamp('2026-09-01',tz='UTC').timestamp()));st=0 if period=='early' else split;en=split if period=='early' else len(ts)
      for scenario,fr,de,adv in [('base',1,1,0),('cost5',5,1,0),('delay2',1,2,0),('combined',5,2,1)]:
       o,path,dd,missing=rotate(A,F,S,st,en,rates,fr/1e4,de,adv,holdh*60)
       # Any position without an executable final quote cannot be silently treated as flat.
       gap=float(path[-1]-100-o[:,11].sum());results.append(dict(horizon=h,holdh=holdh,stop_mult=sm,longonly=longonly,period=period,scenario=scenario,trades=len(o),net=float(o[:,11].sum()),dd=dd,terminal_reconciliation_gap=gap,missing_held=missing,without_best3=float(o[:,11].sum()-np.sort(o[:,11])[-3:].sum())))
       np.savez_compressed(P/'rotation_ledgers'/f'{h}_{holdh}_{sm}_{longonly}_{period}_{scenario}.npz',ledger=o,path=path)
     print(h,holdh,sm,longonly,'done',flush=True)
  pd.DataFrame(results).to_csv(P/'rotation_results.csv',index=False)
