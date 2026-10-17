from pathlib import Path
import pandas as pd,numpy as np
from scipy.optimize import brentq
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';q=pd.read_parquet(V/'long_eligible_prices.parquet').loc['2004-09-13':];pool=pd.read_csv(P/'primary_shortlist.csv');rows=[]
for _,r in pool.iterrows():
 names=[r.a,r.b];prices=q[names].to_numpy();nominal=[]
 for s in names:
  d=pd.read_parquet(V/'long_daily'/(s+'.parquet'));d.index=pd.to_datetime(d.index.date);sp=d['Stock Splits'].replace(0,1);factor=sp.iloc[::-1].cumprod().iloc[::-1]/sp;nominal.append((d.Close*factor).reindex(q.index).to_numpy())
 nominal=np.array(nominal).T;assert np.isfinite(nominal).all() and (nominal>0).all()
 for label,c in [('fixed_10bp',np.full_like(prices,.001)),('fixed_25bp',np.full_like(prices,.0025)),('fixed_100bp',np.full_like(prices,.01)),('one_cent_spread_floor_10bp',np.maximum(.001,.005/nominal)),('two_cent_spread_floor_10bp',np.maximum(.001,.01/nominal))]:
  qty=.5/(1+c[0].mean())/prices[0];nav=[1/(1+c[0].mean())]
  for i in range(1,len(prices)):
   before=qty*prices[i];total=before.sum()
   if i==len(prices)-1:nav.append((before*(1-c[i])).sum());continue
   after=brentq(lambda v:v+np.sum(c[i]*np.abs(.5*v-before))-total,0,total,xtol=1e-12);qty=.5*after/prices[i];nav.append(after)
  nav=np.array(nav);single=prices[-1]/prices[0]*(1-c[-1])/(1+c[0]);dd=(nav/np.maximum.accumulate(np.r_[1,nav])[1:]-1).min()
  rows.append(dict(a=r.a,b=r.b,scenario=label,rebalance_multiple=nav[-1],stock_a_multiple=single[0],stock_b_multiple=single[1],over_best=nav[-1]/max(single),maxdd=dd,max_cost_a_bps=c[:,0].max()*1e4,max_cost_b_bps=c[:,1].max()*1e4))
  if label=='fixed_10bp':assert np.isclose(nav[-1],r.rebalance_multiple,rtol=1e-9)
z=pd.DataFrame(rows);z.to_csv(P/'cost_stresses.csv',index=False);print(z[['a','b','scenario','rebalance_multiple','over_best']].to_string(index=False),flush=True)
