from pathlib import Path
import pandas as pd,numpy as np,json
from scipy.optimize import brentq
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';q=pd.read_parquet(V/'long_eligible_prices.parquet');pool=pd.read_csv(P/'verification_pool.csv');rows=[];curves=[];checks={}
def path(p,w=.5,c=.001):
 weights=np.array([w,1-w]);qty=weights/(1+c)/p[0];out=[1/(1+c)]
 for i in range(1,len(p)):
  before=qty*p[i];total=before.sum()
  if i==len(p)-1:out.append(total*(1-c));continue
  nav=brentq(lambda v:v+c*np.abs(weights*v-before).sum()-total,0,total,xtol=1e-12);qty=weights*nav/p[i];out.append(nav)
 return np.array(out)
# Independent holdings/cash root solve checks the optimized return formula.
known=pd.read_parquet(P/'common22_all_pairs.parquet');ix=q.loc['2004-09-13':].index
for a,b in [('CAR','INSM'),('GFI','INSM'),('HL','INSM'),('INSM','STAA'),('INSM','CNO')]:
 z=q.loc[ix,[a,b]]
 for w in [.5,.55]:
  for c in [.001,.0025]:
   p=path(z.to_numpy(),w,c);v=known[(known.a==a)&(known.b==b)&known.weight_a.eq(w)&known.cost_bps.eq(c*1e4)]
   if len(v)==0:continue
   assert np.isclose(p[-1],v.iloc[0].rebalance_multiple,rtol=1e-9);checks[f'{a}/{b}/{w}/{c}']=True
   if w==.5 and c==.001:curves.append(pd.DataFrame({'date':ix,'a':a,'b':b,'strategy':p,'stock_a':z[a].to_numpy()/z[a].iloc[0]*(1-c)/(1+c),'stock_b':z[b].to_numpy()/z[b].iloc[0]*(1-c)/(1+c)}))
for _,r in pool.iterrows():
 z=q[[r.a,r.b]].dropna();z=z.loc['2004-09-13':]
 if len(z)<100:continue
 for label,lo,hi in [('2004_2014','2004-09-13','2014-09-12'),('2014_2026','2014-09-15','2026-09-11'),('2018_2026','2018-05-01','2026-09-11')]:
  a=z.loc[lo:hi];g=a.to_numpy()[1:]/a.to_numpy()[:-1];v=np.prod(g.mean(1)[:-1]-.001*np.abs(g[:-1,0]-g[:-1,1])*.5)*g[-1].mean()*.999/1.001;end=a.iloc[-1]/a.iloc[0]*.999/1.001;rows.append(dict(a=r.a,b=r.b,period=label,rebalance_multiple=v,stock_a_multiple=end.iloc[0],stock_b_multiple=end.iloc[1],over_best=v/end.max(),over_hold=v/end.mean()))
 for years in [5,10]:
  ratios=[]
  for start in pd.date_range(z.index[0],z.index[-1]-pd.DateOffset(years=years),freq='MS'):
   a=z.loc[start:start+pd.DateOffset(years=years)]
   if len(a)<years*240:continue
   g=a.to_numpy()[1:]/a.to_numpy()[:-1];v=np.prod(g.mean(1)[:-1]-.001*np.abs(g[:-1,0]-g[:-1,1])*.5)*g[-1].mean()*.999/1.001;end=a.iloc[-1]/a.iloc[0]*.999/1.001;ratios.append((v/end.max(),v/end.mean()))
  if ratios:rows.append(dict(a=r.a,b=r.b,period=f'rolling_{years}y',starts=len(ratios),beats_best_fraction=np.mean(np.array(ratios)[:,0]>1),beats_hold_fraction=np.mean(np.array(ratios)[:,1]>1),median_over_best=np.median(np.array(ratios)[:,0]),worst_over_best=np.min(np.array(ratios)[:,0])))
pd.DataFrame(rows).to_csv(P/'period_checks.csv',index=False);pd.concat(curves).to_parquet(P/'primary_wealth_paths.parquet',index=False);(P/'verification.json').write_text(json.dumps(checks,indent=2));print('independent holdings checks',len(checks),'period rows',len(rows),flush=True)
