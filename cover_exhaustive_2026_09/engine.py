import numpy as np
from numba import njit,prange,set_num_threads
set_num_threads(4)
# Exact fixed 50/50 cost identity: post-trade wealth = mean(gross) - c*abs(diff(gross))/2.
@njit(cache=True)
def metrics(a,b,start,end,daynums):
 out=np.full(17,np.nan)
 n=end-start
 if n<1:return out
 years=(daynums[end]-daynums[start])/365.25
 w0=1.;w5=1/1.0005;w10=1/1.001;peak=1.;dd=0.;hp=1.;hdd=0.;turn=1.;sa=0.;sb=0.;saa=0.;sbb=0.;sab=0.
 for t in range(start+1,end+1):
  ga=a[t]/a[t-1];gb=b[t]/b[t-1];mean=.5*(ga+gb);diff=.5*abs(ga-gb)
  w0*=mean
  if t==end:w5*=mean*.9995;w10*=mean*.999;turn+=1.
  else:w5*=mean-.0005*diff;w10*=mean-.001*diff;turn+=diff/mean
  if w5>peak:peak=w5
  dd=min(dd,w5/peak-1.)
  h=(1/1.0005)*.5*(a[t]/a[start]+b[t]/b[start])
  if t==end:h*=.9995
  hp=max(hp,h);hdd=min(hdd,h/hp-1.)
  la=np.log(ga);lb=np.log(gb);sa+=la;sb+=lb;saa+=la*la;sbb+=lb*lb;sab+=la*lb
 h5=(.9995/1.0005)*.5*(a[end]/a[start]+b[end]/b[start]);h10=(.999/1.001)*.5*(a[end]/a[start]+b[end]/b[start])
 out[0]=n;out[1]=years;out[2]=w0**(1/years)-1;out[3]=w5**(1/years)-1;out[4]=w10**(1/years)-1;out[5]=h5**(1/years)-1
 out[6]=((.9995/1.0005)*a[end]/a[start])**(1/years)-1;out[7]=((.9995/1.0005)*b[end]/b[start])**(1/years)-1
 out[8]=dd;out[9]=hdd;out[10]=turn/years;out[11]=np.log(w5/h5)/years;out[12]=np.log(w10/h10)/years;out[13]=np.log(w5)/years;out[14]=np.log(h5)/years
 denom=np.sqrt(max(0.,(saa-sa*sa/n)*(sbb-sb*sb/n)))
 out[15]=(sab-sa*sb/n)/denom if denom>0 else np.nan;out[16]=(np.log(w0)-.5*(sa+sb))/years
 return out
@njit(cache=True,parallel=True)
def compute(prices,aa,bb,daynums,d0,mid,dend,cfend):
 # id fields start/end; full metrics; discovery metrics; half metrics; confirmation metrics.
 out=np.full((len(aa),2+17*5),np.nan)
 for k in prange(len(aa)):
  a=prices[aa[k]];b=prices[bb[k]];beststart=-1;bestlen=0;runstart=0;run=0
  for t in range(len(a)):
   if np.isfinite(a[t]) and np.isfinite(b[t]) and a[t]>0 and b[t]>0:
    if run==0:runstart=t
    run+=1
    if run>bestlen:bestlen=run;beststart=runstart
   else:run=0
  if bestlen>=253:
   out[k,0]=beststart;out[k,1]=beststart+bestlen-1;out[k,2:19]=metrics(a,b,beststart,beststart+bestlen-1,daynums)
  good=True
  for t in range(d0,dend+1):
   if not np.isfinite(a[t]) or not np.isfinite(b[t]) or a[t]<=0 or b[t]<=0:good=False;break
  if good:
   out[k,19:36]=metrics(a,b,d0,dend,daynums);out[k,36:53]=metrics(a,b,d0,mid,daynums);out[k,53:70]=metrics(a,b,mid,dend,daynums)
  end=dend
  for t in range(dend,cfend+1):
   if not np.isfinite(a[t]) or not np.isfinite(b[t]) or a[t]<=0 or b[t]<=0:break
   end=t
  if end>dend:out[k,70:87]=metrics(a,b,dend,end,daynums)
 return out
