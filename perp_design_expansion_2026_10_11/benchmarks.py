from slow import *
if __name__=='__main__':
 rows=[]
 for t in ['KXZECPERP','KXBTCPERP','KXETHPERP','KXSOLPERP','KXDOGEPERP']:
  ts,A,F,_=data_load(t)
  for period in ['early','later']:
   split=np.searchsorted(ts,int(pd.Timestamp('2026-09-01',tz='UTC').timestamp()));st=0 if period=='early' else split;en=split if period=='early' else min(len(ts),np.searchsorted(ts,1791590400))
   valid=np.isfinite(A[:,:2].sum(axis=1));ix=np.flatnonzero(valid& (np.arange(len(ts))>=st)&(np.arange(len(ts))<en))
   if not len(ix):continue
   st=ix[0];en=ix[-1]
   for side in [1,-1]:
    ep=A[st,1] if side>0 else A[st,0];qty=int(50/ep);prices=A[ix,0] if side>0 else A[ix,1];g=side*qty*(prices-ep);fund=-side*qty*(F[ix]-F[st]);fees=qty*(ep+prices)*r.fees['taker_fee_rates'][t];slip=qty*(ep+prices)*.0001;path=100+g+fund-fees-slip;dd=float(np.max(np.maximum.accumulate(np.r_[100,path])[1:]-path));rows.append(dict(ticker=t,period=period,side=side,qty=qty,net=float(path[-1]-100),dd=dd,start=int(ts[st]),end=int(ts[en]),entry_notional=qty*ep))
 pd.DataFrame(rows).to_csv(P/'passive_benchmarks.csv',index=False);print(pd.DataFrame(rows).to_string(index=False))
