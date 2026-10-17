import numpy as np
def simulate(C,O,F,signal,source,stress,st,en,chosen,fundscale=1.,fixed_hold=False):
 entered=np.zeros(C.shape[1],dtype=np.bool_);exposure_sum=0.;max_exposure=0.;cash=100.;qty=np.zeros(C.shape[1]);last=np.full(C.shape[1],np.nan);lastf=np.zeros(C.shape[1]);peak=100.;dd=0.;turn=0.;fees=0.;fund=0.;n=0;missing=0;path=[];pnls=np.zeros(C.shape[1]);fr=.0005 if stress else .0001;fee=.0012
 B=O.iloc[:,::2].to_numpy();A=O.iloc[:,1::2].to_numpy();FF=F.to_numpy()
 for i in range(st,en):
  bid=B[i];ask=A[i];mid=(bid+ask)/2;valid=np.isfinite(mid);daily=np.zeros(C.shape[1])
  for j in range(C.shape[1]):
   if qty[j]!=0:
    if valid[j] and np.isfinite(last[j]):daily[j]+=qty[j]*(mid[j]-last[j])
    if not valid[j]:missing+=1
    if source=='proxy':charge=abs(qty[j])*(mid[j] if valid[j] else last[j])*(.0003 if stress else .0001)*3
    else:charge=qty[j]*(FF[i,j]-lastf[j]) if np.isfinite(FF[i,j]) else 0.
    charge*=fundscale;daily[j]-=charge;fund-=charge
   if valid[j]:last[j]=mid[j]
   if np.isfinite(FF[i,j]):lastf[j]=FF[i,j]
  cash+=daily.sum();pnls+=daily;target=np.zeros(C.shape[1])
  for j in chosen:
   if valid[j]:target[j]=np.sign(signal[i,j])*int(abs(signal[i,j])*.5*max(0,cash)/len(chosen)/mid[j]) if i<en-1 else 0
   else:target[j]=qty[j]
   if fixed_hold:
    target[j]=qty[j]
    if not entered[j] and valid[j] and signal[i,j]>0:
     target[j]=int(50./len(chosen)/mid[j]);entered[j]=True
    if i==en-1 and valid[j]:target[j]=0
  for j in chosen:
   if not valid[j]:continue
   delta=target[j]-qty[j]
   if delta:
    px=ask[j] if delta>0 else bid[j];cost=abs(delta)*(px*(fee+fr)+abs(px-mid[j]));cash-=cost;pnls[j]-=cost;fees+=cost;turn+=abs(delta)*px;n+=1
   qty[j]=target[j]
  notional=(abs(qty)*np.nan_to_num(last)).sum();exposure=notional/max(cash,1e-12);exposure_sum+=exposure;max_exposure=max(max_exposure,exposure)
  peak=max(peak,cash);dd=max(dd,peak-cash);path.append(cash)
 remaining=abs(qty).sum()
 if remaining:
  cost=(abs(qty)*np.nan_to_num(last)*(fee+fr)).sum();cash-=cost;fees+=cost;pnls-=abs(qty)*np.nan_to_num(last)*(fee+fr);path[-1]=cash;dd=max(dd,peak-cash)
 assert abs(pnls.sum()-(cash-100))<1e-7
 return dict(mean_exposure=exposure_sum/max(1,en-st),max_sampled_exposure=max_exposure,net=cash-100,dd=dd,orders=n,turnover=turn,fees_friction=fees,funding=fund,missing_held=missing,stale_terminal=bool(remaining)),path,pnls