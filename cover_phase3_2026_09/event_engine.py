"""Exit study FSM. Policy is committed with the signal, then retained."""
import numpy as np
from numba import njit
@njit(cache=True)
def run(o,h,l,c,roll,atr,signal,weights,ema48,ema192,start,choice,low5,update,fee=3.5,mult=2.,slip=0.):
 n=len(c);path=np.zeros((n,6));records=np.zeros((n,9));nr=0;events=np.zeros((n,5))
 qty=pending=policy=ppolicy=0;pending_exit=False;entry=hw=stop=soft=pa=entry_atr=0.;ei=0
 gross=fees=entered=0.
 for i in range(n):
  px=-1.;reason=0
  if roll[i]:
   pending=0
   if qty:px=c[i-1];reason=2
  elif qty and pending_exit:px=o[i];reason=3
  pending_exit=False
  if px>=0:
   px-=slip;events[i,2]=qty;events[i,3]=px;g=(px-entry)*mult*qty;gross+=g;fees+=fee/2*qty
   records[nr]=np.array([ei,i,entry,px,qty,g-fee*qty,reason,px-entry,policy]);nr+=1;qty=0
  if pending:
   qty=pending;pending=0;policy=ppolicy;entry_atr=pa;entry=hw=o[i]+slip;ei=i
   events[i,0]=qty;events[i,1]=entry
   k=4. if policy in (3,8) else 3. if policy==7 else 2.
   stop=entry-k*pa;soft=entry-2*pa;fees+=fee/2*qty;entered+=qty
  if qty:
   if l[i]<=stop:
    px=min(o[i],stop)-slip;events[i,2]=qty;events[i,3]=px;g=(px-entry)*mult*qty;gross+=g;fees+=fee/2*qty
    records[nr]=np.array([ei,i,entry,px,qty,g-fee*qty,1,px-entry,policy]);nr+=1;qty=0
   else:
    hw=max(hw,h[i])
    if update[i]:
     if policy==3:
      if c[i]<=soft:pending_exit=True
      soft=max(soft,hw-2*atr[i])
     elif policy==4:stop=max(stop,hw-2*entry_atr)
     elif policy==5:
      if np.isfinite(low5[i]):stop=max(stop,low5[i])
     elif policy==6:
      if hw-entry>=2*entry_atr:stop=max(stop,hw-2*atr[i])
     else:
      k=4. if policy==8 or (policy==2 and ema48[i]>ema192[i]) else 3. if policy==7 else 2.
      stop=max(stop,hw-k*atr[i])
  if not qty and signal[i] and i>=start and np.isfinite(atr[i]):
   pending=max(0,int(weights[i]));pa=atr[i];ppolicy=choice[i]
  events[i,4]=qty
  unreal=(c[i]-entry)*mult*qty
  path[i]=np.array([gross+unreal-fees,gross+unreal,fees,qty,entered,max(0.,c[i]-stop)*mult*qty])
 return path,records[:nr],events

