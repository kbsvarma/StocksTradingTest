import numpy as np
def traced_crp(bid,ask,step,fee,integer,capital=10000.,bidcap=None,askcap=None,exit_bid=None,exit_cap=None):
 trace=[capital];lastmark=bid[0].copy();q=np.zeros(2);cash=capital;turn=0.;count=0;fixed_target=np.zeros(2)
 for i in range(1,len(bid)-1):
  lastmark=np.where(np.isfinite(bid[i]),bid[i],lastmark);trace.append(cash+np.sum(q*lastmark)*100)
  if not np.isfinite(bid[i]).all() or not np.isfinite(ask[i]).all():continue
  if i>1 and step>=0 and (step==0 or (i-1)%step):continue
  # Previous observable mid determines quantity; next quote executes it.
  mark=(bid[max(0,i-1)]+ask[max(0,i-1)])/2
  if not np.isfinite(mark).all() or np.min(mark)<=0:continue
  nav=cash+np.sum(q*mark)*100;target=.5*nav/(mark*100)
  if integer:target=np.floor(target)
  if i==1:fixed_target=target.copy()
  if step==-1:target=fixed_target.copy()
  diff=target-q
  for j in range(2):
   if diff[j]<0:
    amt=-diff[j]
    if bidcap is not None:amt=min(amt,bidcap[i,j])
    cash+=amt*(bid[i,j]*100-fee);q[j]-=amt;turn+=amt;count+=1
  for j in range(2):
   if diff[j]>0:
    amt=min(diff[j],max(0,cash-fee*q.sum())/(ask[i,j]*100+2*fee))
    if askcap is not None:amt=min(amt,askcap[i,j])
    if integer:amt=np.floor(amt)
    cash-=amt*(ask[i,j]*100+fee);q[j]+=amt;turn+=amt;count+=amt>0
 assert cash>=-1e-5
 if exit_bid is not None:
  for ii in range(len(exit_bid)):
   for j in range(2):
    if not np.isfinite(exit_bid[ii,j]):continue
    amt=min(q[j],exit_cap[ii,j]);cash+=amt*(exit_bid[ii,j]*100-fee);q[j]-=amt;turn+=amt
  trace.append(cash);return cash-capital,turn,count,q.sum(),np.array(trace)
 sold=q if bidcap is None else np.minimum(q,bidcap[-1]);left=np.sum(q-sold);cash+=np.sum(sold*(bid[-1]*100-fee));turn+=sold.sum();trace.append(cash);return cash-capital,turn,count,left,np.array(trace)
