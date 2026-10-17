import numpy as np
from run import run,features
import pandas as pd
# Causality: appending/mutating future prices cannot alter earlier features.
c=pd.Series(np.exp(np.linspace(0,.2,250)+.01*np.sin(np.arange(250))),index=np.arange(250)*3600)
d=c.copy();d.iloc[150:]*=3
for a,b in zip(features(c),features(d)):assert np.allclose(a[:150],b[:150],equal_nan=True)
# Hourly trailing watermark cannot use high 120 to stop out earlier inside same bar.
A=np.array([[100,100,100,100,100,100,1,100],[100,100,95,120,120,95,1,105],[105,105,104,106,106,104,1,105],[105,105,104,106,106,104,1,105],[105,105,104,106,106,104,1,105]],float)
A[:,:6]*=.01;A[:,7]*=.01
S=np.zeros((5,4));S[:,2:]=np.nan;S[0,:2]=[1,.1]
o,cash,dd,missing=run(A,S,np.zeros(5),np.arange(5)*3600,0,5,1,0,0,1,0,True,0)
assert len(o)==1 and o[0,11]==4 and abs(o[0,5]-1.05)<1e-8
# Stop wins ambiguous bar, preventing favorable target selection.
A[1,2]=.8;A[1,3]=A[1,4]=1.5
o,*_=run(A,S,np.zeros(5),np.arange(5)*3600,0,5,0,0,0,1,0,True,0)
assert o[0,11]==1 and abs(o[0,5]-.9)<1e-8
# Venue entry cannot use low preceding its fill.
A[1,2]=.8;A[1,3]=1.;A[1,4]=1.
S[1:,0]=99
o,cash,*_=run(A[:,:7],S,np.zeros(5),np.arange(5)*60,0,5,0,0,0,1,0,False,0)
assert o[0,11]==4 and abs(cash-100-o[:,10].sum())<1e-8
print('PASS: feature causality, non-retroactive trailing, adverse ambiguous bar, entry-bar timing, cash reconciliation')
from portfolio import simulate
ix=pd.date_range('2022-01-01',periods=3,tz='UTC');C=pd.DataFrame(np.ones((3,4)),index=ix);O=pd.DataFrame(np.ones((3,8)),index=ix);F=pd.DataFrame(np.zeros((3,4)),index=ix)
r,p,q=simulate(C,O,F,np.ones((3,4)),'proxy',False,0,3,list(range(4)))
assert abs(r['net']+.1536)<1e-8
r,p,q=simulate(C,O,F,np.zeros((3,4)),'proxy',False,0,3,list(range(4)));assert r['net']==0 and r['orders']==0
print('PASS: shared100budget, integerqty, roundtripfees, heldfunding, zeroexposure')
