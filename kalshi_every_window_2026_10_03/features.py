"""Frozen, causal minute features shared by historical validation and paper."""
import math
import numpy as np
def logit(p):
    p=min(.999,max(.001,p));return math.log(p/(1-p))
def statistics(bars,T):
    times=range(T-61*60,T,60)
    if any(t not in bars for t in times):return None
    v=np.array([bars[t] for t in times],dtype=float)
    if not np.isfinite(v).all() or (v<=0).any():return None
    ret=np.diff(np.log(v));sigma=float(ret.std())
    if sigma<=0:return None
    return (float(v[-1]),sigma,float(ret[-15:].std()/sigma),*[float(ret[-n:].sum()/(sigma*math.sqrt(n))) for n in (1,3,5,15,60)])
def build(T,close,start,bid,ask,previous_quote,bars,other_bars):
    rem=(close-T)//60
    if not 1<=rem<=14 or T%60 or not 0<=bid<=ask<=1:return None
    s=statistics(bars,T);o=statistics(other_bars,T);ref=bars.get(start-60)
    if s is None or o is None or not ref:return None
    z=(s[0]/ref-1)/(s[1]*math.sqrt(rem));mid=(bid+ask)/2;lm=logit(mid);lr=math.log(rem/7)
    dp=lm-logit(sum(previous_quote)/2) if previous_quote else 0
    ratio=math.log(max(.05,min(20,s[2])))
    x=[1,lm,lm*abs(lm),lm*lr,max(-6,min(6,z)),z*ratio,
        *[max(-6,min(6,q)) for q in s[3:]],max(-6,min(6,o[4])),dp]
    return dict(t=T,rem=rem,b=bid,a=ask,mid=mid,z=z,sigma=s[1],spot=s[0],ref=ref,xrich=x)
def probability(f,model):
    x=np.array(f['xrich'])/np.array(model['scale']);coef=np.array(model['coef'])
    return float(1/(1+np.exp(-np.clip(np.einsum('i,i',x,coef),-30,30))))
