"""Independent share-ledger reconciliation and theoretical identities."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.optimize import brentq
P=Path(__file__).resolve().parent
price=pd.read_csv(P/'adjusted_prices.csv',index_col=0,parse_dates=True);daily=pd.read_parquet(P/'daily.parquet');details=json.loads((P/'details.json').read_text());names=details['names'];checks=[]
# Buy/hold and CRP calculated independently as share counts against price levels.
for model,wa in [(f'{names[0]}_buy_hold',1.),(f'{names[1]}_buy_hold',0.),('pair_buy_hold_50_50',.5),('daily_rebalance_50_50',.5)]:
 for bps in [0,1,5,10]:
  c=bps/10000;target=np.array([wa,1-wa]);shares=100000*(1-c)*target/price.iloc[0].to_numpy();curve=[]
  for i in range(1,len(price)):
   prices=price.iloc[i].to_numpy();positions=shares*prices;before=positions.sum()
   if i==len(price)-1:value=before*(1-c)
   elif model=='daily_rebalance_50_50':
    value=brentq(lambda v:v+c*np.abs(v*target-positions).sum()-before,before*(1-2*c)-1e-6,before+1e-6,xtol=1e-8)
    shares=value*target/prices
   else:value=before
   curve.append(value)
  saved=daily[(daily.strategy==model)&(daily.cost_bps==bps)].wealth.to_numpy()
  err=float(np.max(np.abs(saved-np.array(curve))));assert np.allclose(saved,curve,rtol=1e-10,atol=1e-6)
  checks.append(dict(strategy=model,cost_bps=bps,max_dollar_error=err))
# Exact lecture cash/hot-stock model: balanced +100%/-50% sequence.
g=np.array([[1.,2.],[1.,.5]]*500);wealth=np.prod(g.mean(axis=1))
assert abs(wealth**(1/1000)-np.sqrt(1.125))<1e-12
# Equal ending prices can produce gain under rebalancing.
g2=np.array([[1.25,.8],[.8,1.25]])
assert np.allclose(g2.prod(axis=0),1)
assert abs(np.prod(g2.mean(axis=1))-1.050625)<1e-12
# Persistent winner: buy/hold can beat fixed rebalancing.
g3=np.array([[2.,1.],[2.,1.]])
assert np.prod(g3.mean(axis=1))==2.25 and g3.prod(axis=0).mean()==2.5
# Universal identity evaluated directly as mean of expert wealth with trapezoid prior.
g=price.pct_change().iloc[1:].to_numpy()+1;b=np.linspace(0,1,1001);weights=np.ones(1001)/1000;weights[[0,-1]]*=.5
experts=np.prod(g[:,[0]]*b+g[:,[1]]*(1-b),axis=0);theoretical=weights@experts
observed=daily[(daily.strategy=='cover_universal_1001')&(daily.cost_bps==0)].wealth.iloc[-1]/100000
assert np.isclose(theoretical,observed,rtol=1e-11)
assert abs(details['universal_grid_terminal_relative_error'])<1e-4
# Historical optimum checked across a separate 10,001-point grid and includes boundaries.
b=np.linspace(0,1,10001);scores=np.log(g[:,[0]]*b+g[:,[1]]*(1-b)).sum(axis=0)
best=b[scores.argmax()];assert abs(best-details['hindsight_weight_a'])<=.00011
# Cost monotonicity and proper target weights on all non-liquidation CRP dates.
s=pd.read_csv(P/'summary.csv')
for _,q in s.groupby('strategy'):assert np.all(np.diff(q.sort_values('cost_bps').terminal_wealth)<=1e-6)
crp=daily[daily.strategy=='daily_rebalance_50_50']
for _,q in crp.groupby('cost_bps'):assert np.allclose(q.weight_a.iloc[:-1],.5)
result={'status':'PASS','independent_share_ledger_checks':checks,'lecture_example_geometric_gain':float(np.sqrt(1.125)-1),'universal_direct_mixture_error':float(theoretical/observed-1),'fine_grid_relative_difference':details['universal_grid_terminal_relative_error'],'hindsight_grid_weight_a':best,'verified':['lecture example','equal-endpoint example','persistent-winner counterexample','16 share-ledger curves','universal mixture identity','finer quadrature','hindsight boundary optimum','cost monotonicity','daily constant weights']}
(P/'verification.json').write_text(json.dumps(result,indent=2));print('PASS',len(checks),'independent share-ledger curves; max error',max(x['max_dollar_error'] for x in checks))
