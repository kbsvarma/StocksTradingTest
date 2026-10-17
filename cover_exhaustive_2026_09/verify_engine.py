from pathlib import Path
import numpy as np,json
from scipy.optimize import brentq
from engine import metrics,compute
P=Path(__file__).resolve().parent
rng=np.random.default_rng(109132026);errors=[]
def ledger(prices,c,target=.5):
 shares=(100000/(1+c))*np.array([target,1-target])/prices[0];wealth=[100000.]
 for i,px in enumerate(prices[1:]):
  vals=shares*px;before=vals.sum()
  if i==len(prices)-2:shares*=1-c
  else:
   t=np.array([target,1-target]);after=brentq(lambda v:v+c*np.abs(v*t-vals).sum()-before,0,before,xtol=1e-8)
   shares=after*t/px
  wealth.append(float((shares*px).sum()))
 return np.array(wealth)
for k in range(30):
 gross=np.exp(rng.normal(0,.035,size=(300,2)));prices=np.vstack([np.ones(2),np.cumprod(gross,axis=0)]);daynums=np.arange(301,dtype=np.int64)
 m=metrics(prices[:,0],prices[:,1],0,300,daynums)
 for c,col in [(0,2),(.0005,3),(.001,4)]:
  w=ledger(prices,c);expected=(w[-1]/100000)**(365.25/300)-1
  err=abs(expected-m[col]);errors.append(err);assert err<1e-10
  if c==.0005:assert abs((w/np.maximum.accumulate(w)-1).min()-m[8])<1e-10
 assert m[2]>=m[3]>=m[4]
# No relative movement means no intermediate trades, only entry and exit.
a=np.exp(np.arange(301)*.001);d=np.arange(301,dtype=np.int64);m=metrics(a,a,0,300,d)
assert abs(m[3]-m[5])<1e-12;assert abs(m[10]-2/(300/365.25))<1e-12
# Cover cash + doubling/halving example.
a=np.array([1.,2.,1.]);b=np.ones(3);m=metrics(a,b,0,2,np.arange(3,dtype=np.int64));assert abs((1+m[2])**(2/365.25)-1.125)<1e-12
# Missing price splits history; never bridge the gap.
prices=np.ones((2,600));prices[0,300]=np.nan
res=compute(prices,np.array([0]),np.array([1]),np.arange(600,dtype=np.int64),0,200,400,599)
assert res[0,0]==0 and res[0,1]==299 and np.isnan(res[0,19])
(P/'engine_verification.json').write_text(json.dumps(dict(independent_share_ledgers=90,max_cagr_absolute_error=max(errors),equal_returns_no_rebalance=True,cover_toy_identity=True,missing_gap_not_bridged=True),indent=2));print('PASS',max(errors),flush=True)
