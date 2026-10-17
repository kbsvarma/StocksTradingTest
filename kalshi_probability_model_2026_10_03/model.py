"""Pure offline settlement forecasts; no network, credentials or order client."""
import math
import numpy as np

def sigmoid(x):return 1/(1+np.exp(-np.clip(x,-35,35)))
def logit(p):
    p=np.clip(p,.0001,.9999)
    return np.log(p/(1-p))
def horizon(sigma,remaining):
    if np.any(np.asarray(remaining)<1):raise ValueError('Only pre-final-minute forecasts supported')
    return np.asarray(sigma)*np.sqrt(np.asarray(remaining)-2/3)
def gaussian(threshold):return np.array([.5*math.erfc(float(v)/math.sqrt(2)) for v in np.ravel(threshold)]).reshape(np.shape(threshold))
def empirical_fit(residual,weight):
    x=np.r_[residual,-residual];w=np.r_[weight,weight]/2
    ix=np.argsort(x);x=x[ix];w=w[ix];c=np.r_[0,np.cumsum(w)/w.sum()];c[-1]=1
    return x,c
def empirical_probability(threshold,x,c):
    lo=np.searchsorted(x,threshold,'left');hi=np.searchsorted(x,threshold,'right')
    return np.clip(1-(c[lo]+c[hi])/2,.0001,.9999)
def fit_blend(mid,physical,y,w,ridge=10):
    X=np.column_stack((logit(mid),logit(physical)));beta=np.array([1.,0.]);prior=beta.copy()
    def loss(b):
        z=X@b
        return float(np.sum(w*(np.logaddexp(0,z)-y*z))+.5*ridge*np.sum((b-prior)**2))
    for _ in range(200):
        p=sigmoid(X@beta);g=X.T@(w*(p-y))+ridge*(beta-prior)
        H=(X.T*(w*p*(1-p)))@X+ridge*np.eye(2)
        step=np.linalg.solve(H,g);alpha=1.
        while alpha>1e-10:
            new=np.maximum(0,beta-alpha*step)
            if loss(new)<=loss(beta)+1e-12:break
            alpha/=2
        if np.max(abs(new-beta))<1e-9:beta=new;break
        beta=new
    return beta
def blend(mid,physical,beta):return sigmoid(beta[0]*logit(mid)+beta[1]*logit(physical))
def debit(price,quantity=1):
    p=np.asarray(price);fee=np.ceil(.07*quantity*p*(1-p)*1e6-1e-8)/1e6
    return np.ceil((quantity*p+fee)/.0001-1e-8)*.0001
def qualifies(probability,ask,spread,lo=.55,hi=.65):
    return np.isfinite(probability+ask+spread)&(spread>=0)&(spread<=.02000001)&(ask>=lo-1e-8)&(ask<=hi+1e-8)&(probability-debit(ask)-.0025>=.03-1e-12)
