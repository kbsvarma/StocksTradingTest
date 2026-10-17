from study import *
from numba import njit
import itertools
@njit
def run(A,F,S,st,en,fr,de,adverse,fee0,fee1,trend):
 out=np.zeros((en-st+1,16));n=0;cash=100.;peak=100.;dd=0.;pending=0;due=0;pos=0;cool=st;side=0;beta=0.;mu=0.;sd=0.;ei=0;ey=0.;ex=0.;qy=0;qx=0;reason=0;risk=0.;entryres=0.;missing=0;last=100.
 for i in range(st,en):
  valid=np.isfinite(A[i,0,0]+A[i,0,1]+A[i,1,0]+A[i,1,1]);closed=False
  if pos and not valid:missing+=1
  if pending==1 and i>=due:
   if valid:
    ey=A[i,0,1] if side>0 else A[i,0,0];ex=A[i,1,0] if side>0 else A[i,1,1]
    risk=min(2.,.02*cash);cost=2*(fee0+beta*fee1+fr*(1+beta));notional=min(.5*cash/(1+beta),risk/(2*sd+cost));qy=int(notional/ey);qx=int(beta*qy*ey/ex)
    if qy>0 and qx>0 and abs(qx*ex/(beta*qy*ey)-1)<=.2:
     pos=1;ei=i;entryres= np.log((A[i,0,0]+A[i,0,1])/2)-beta*np.log((A[i,1,0]+A[i,1,1])/2)
   pending=0
  if pos and valid:
   py=A[i,0,0] if side>0 else A[i,0,1];px=A[i,1,1] if side>0 else A[i,1,0];gross=side*(qy*(py-ey)-qx*(px-ex));fund=-side*(qy*(F[i,0]-F[ei,0])-qx*(F[i,1]-F[ei,1]));fees=qy*(ey+py)*fee0+qx*(ex+px)*fee1;slip=(qy*(ey+py)+qx*(ex+px))*fr;net=gross+fund-fees-slip
   if (pending==2 and i>=due) or i==en-1:
    if pending==2 and reason==1 and adverse and net>-risk:slip+=net+risk;net=-risk
    if i==en-1:reason=4
    out[n]=np.array([ei,i,side,qy,qx,ey,ex,py,px,gross,fund,fees,slip,net,reason,beta]);n+=1;cash+=net;pos=0;pending=0;cool=i+60;closed=True
   elif pending==0:
    bady=A[i,0,2] if side>0 else A[i,0,3];badx=A[i,1,3] if side>0 else A[i,1,2];bad=side*(qy*(bady-ey)-qx*(badx-ex))+fund-fees-slip if np.isfinite(bady+badx) and i>ei else net
    res=np.log((A[i,0,0]+A[i,0,1])/2)-beta*np.log((A[i,1,0]+A[i,1,1])/2);reason=0
    if min(net,bad)<=-risk:reason=1
    elif (not trend and side*(res-mu)>=0) or (trend and side*(res-entryres)>=2*sd):reason=2
    elif i-ei>=1440:reason=3
    if reason:pending=2;due=i+de
   eq=cash if not pos else cash+net;last=eq
  elif not pos:last=cash
  peak=max(peak,last);dd=max(dd,peak-last)
  if not pos and pending==0 and not closed and i>=cool and i<en-de-2 and valid and S[i,0]!=0:
   side=S[i,0];beta=S[i,1];mu=S[i,2];sd=S[i,3];pending=1;due=i+de
 return out[:n],dd,missing

def inputs(a,b,cache,h,trend):
 ty,Y,Fy=cache[a];tx,X,Fx=cache[b];ts=np.arange(max(ty[0],tx[0]),min(ty[-1],tx[-1])+1,60);Y=Y[np.searchsorted(ty,ts)];X=X[np.searchsorted(tx,ts)];F=np.c_[Fy[np.searchsorted(ty,ts)],Fx[np.searchsorted(tx,ts)]];A=np.stack([Y,X],axis=1);sel=ts%300==0;lp=pd.DataFrame({'y':np.log(Y[sel,:2].mean(axis=1)),'x':np.log(X[sel,:2].mean(axis=1))},index=ts[sel]);lp.loc[lp.isna().any(axis=1),:]=np.nan;w=h*12;rr=lp.rolling(w,min_periods=int(np.ceil(.9*w)));m=rr.mean().shift(1);v=rr.var(ddof=0).shift(1);cov=lp.y.rolling(w,min_periods=int(np.ceil(.9*w))).cov(lp.x,ddof=0).shift(1);beta=cov/v.x;mu=m.y-beta*m.x;sd=np.sqrt((v.y+beta*beta*v.x-2*beta*cov).clip(lower=0));z=(lp.y-beta*lp.x-mu)/sd;S=np.zeros((len(ts),4))
 for stamp in lp.index:
  be=beta.loc[stamp];sig=z.loc[stamp];stdev=sd.loc[stamp]
  if not np.isfinite(be+sig+stdev) or not .25<=be<=4 or abs(sig)<2 or stdev<=0:continue
  i=np.searchsorted(ts,stamp)
  if min(A[i,0,6],A[i,1,6])<=0:continue
  if any((A[i,j,1]-A[i,j,0])/A[i,j,:2].mean()>.005 for j in range(2)):continue
  side=np.sign(sig)*(1 if trend else -1);S[i]=[side,be,mu.loc[stamp],stdev]
 return ts,A,F,S
if __name__=='__main__':
 (P/'pair_ledgers').mkdir(exist_ok=True)
 cache={};crypto=['KXBTCPERP','KXDOGEPERP','KXLTCPERP','KXNEARPERP','KXSOLPERP','KXUNIPERP','KXWLDPERP'];metals=['KXGOLDPERP','KXSILVERPERP','KXPLATINUMPERP','KXPALLADIUMPERP'];rows=[]
 for t in crypto+metals:cache[t]=data_load(t)[:3]
 for a,b in list(itertools.combinations(crypto,2))+list(itertools.combinations(metals,2)):
  for h in [6,24,72]:
   for trend in [0,1]:
    ts,A,F,S=inputs(a,b,cache,h,trend);split=int(pd.Timestamp('2026-09-01' if a in crypto else '2026-10-01',tz='UTC').timestamp())
    for period in ['early','later']:
     st=0 if period=='early' else np.searchsorted(ts,split);en=np.searchsorted(ts,split) if period=='early' else len(ts);en=min(en,len(ts))
     while en>st and not np.isfinite(A[en-1,:,:2].sum()):en-=1
     if en<=st:continue
     for scenario,fr,de,adv in [('base',1,1,0),('cost5',5,1,0),('delay2',1,2,0),('combined',5,2,1)]:
      o,dd,missing=run(A,F,S,st,en,fr/1e4,de,adv,r.fees['taker_fee_rates'][a],r.fees['taker_fee_rates'][b],trend);np.savez_compressed(P/'pair_ledgers'/f'{a}_{b}_{h}_{trend}_{period}_{scenario}.npz',ledger=o);rows.append(dict(a=a,b=b,hours=h,trend=trend,period=period,scenario=scenario,trades=len(o),net=float(o[:,13].sum()),dd=dd,without_best3=float(o[:,13].sum()-np.sort(o[:,13])[-3:].sum()),missing_held=missing,days=(en-st)/1440))
  print(a,b,'done',flush=True);pd.DataFrame(rows).to_csv(P/'pair_results.csv',index=False)
