from pathlib import Path
import numpy as np,pandas as pd,json
from round_a import sim
from round_b import allocate
from round_c import inputs,replay,MULT,TICK
P=Path(__file__).resolve().parent;checks={}
# Cash conservation and no volatility harvest when both legs move identically.
pr=np.ones((100,2))*100;elig=np.ones(pr.shape,dtype=bool);flag=np.ones(100,dtype=bool)
for mode in [0,1,2,3,4]:
 nav,_,_=sim(pr,elig,flag,mode,0.,.05);assert np.allclose(nav,1.)
pr=np.repeat(np.exp(np.arange(100)*.001)[:,None],2,axis=1)
a=sim(pr,elig,flag,0,0)[0];b=sim(pr,elig,flag,1,0)[0];assert np.allclose(a,b)
checks['stock_conservation_and_identical_returns']=True
pr=np.column_stack([1+.01*np.sin(np.arange(100)),1+.02*np.cos(np.arange(100))]);cost=np.full(pr.shape,.001)
for mode in [0,1,2,3,5]:
 full=allocate(pr,cost,flag,mode)[0];cut=allocate(pr[:60],cost[:60],flag[:60],mode)[0];assert np.allclose(full[:59],cut[:59]);assert np.isfinite(full).all()
checks['allocation_prefix_no_future_input']=True
prices=pd.read_parquet(P.parent/'cover_validation_2026_09/long_eligible_prices.parquet')
for parent in ['baseline','ridge10','width20']:
 for case,fee,slip in [('stress',7.,1),('severe',14.,2)]:
  ix,ev,cl,ref,di,ex=inputs(parent,case,prices,'2018-05-01','2026-07-02');loc=np.flatnonzero(di>=0);flags=np.ones(len(ix),dtype=bool)
  p,*_=replay(ev,cl,ref,di,ex,np.ones((len(ix),2)),flags,50000.,0,False,fee,slip,.001,1.)
  init=(ev[0,:,4]*(fee/2+slip*TICK*MULT)).sum();end=(ev[-1,:,4]*(fee/2+slip*TICK*MULT)).sum();expected=ref[loc]-init;expected[-1]-=end;err=np.max(np.abs(p[:,0]-expected));assert err<1e-6,(parent,case,err)
  checks[parent+'_'+case+'_cash_replay_parity']=float(err)
  # Truncate after a completed stock-close boundary; prior curve is unchanged.
  last=loc[min(500,len(loc)-1)];nn=di[last]+1
  q,*_=replay(ev[:last+1],cl[:last+1],ref[:last+1],di[:last+1],ex[:last+1],prices.loc[ix[:nn],['ALLE','KR']].to_numpy(),flags[:nn],100000.,2,True,fee,slip,.001)
  allp,*_=replay(ev,cl,ref,di,ex,prices.loc[ix,['ALLE','KR']].to_numpy(),flags,100000.,2,True,fee,slip,.001)
  assert np.allclose(q[:-1,0],allp[:nn-1,0]);checks[parent+'_'+case+'_prefix']=True
(P/'verification.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
