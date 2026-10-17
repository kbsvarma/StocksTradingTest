from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;rows=[];optrows=[]
def score(z):
 z=z.sort_values('day');net=z.net;eq=net.cumsum();out=dict(n=len(z),net=net.sum(),mean=net.mean(),median=net.median(),worst=net.min(),dd=(eq-eq.cummax().clip(lower=0)).min(),win=(net>0).mean(),first=z.day.min(),last=z.day.max())
 if 'fill_increment' in z:out['mean_fill_increment']=z.fill_increment.mean()
 if 'increment' in z:
  out['mean_increment']=z.increment.mean();months=z.assign(month=z.day.str[:7]).groupby('month').increment.agg(['sum','count']);rng=np.random.default_rng(1729);draw=rng.integers(len(months),size=(2000,len(months)));boot=months['sum'].to_numpy()[draw].sum(1)/months['count'].to_numpy()[draw].sum(1);out['increment_ci_low'],out['increment_ci_high']=np.quantile(boot,[.025,.975])
 for col in ['option_pnl','hedge_pnl','hedge_cost','premium','shares_traded','max_hedge_notional','valid_delta_fraction']:
  if col in z:out['mean_'+col]=z[col].mean()
 return out
for sym in ['spy','iwm']:
 x=pd.read_parquet(P/f'{sym}_hedge_trades.parquet');ctrl=x[x.design=='static_delta'][['day','tenor','case','net']].rename(columns={'net':'control_net'});x=x.merge(ctrl,on=['day','tenor','case']);x['increment']=x.net-x.control_net
 for (tenor,case,design),g in x.groupby(['tenor','case','design']):
  for period,lo,hi in [('all','2021','2027'),('early','2021','2023'),('middle','2023','2025'),('recent','2025','2027')]:
   z=g[g.day.ge(lo)&g.day.lt(hi)]
   for coverage in ['all_conservative','complete_exit','valid_model_complete']:
    zz=z if coverage=='all_conservative' else z[~z.missing_exit]
    if coverage=='valid_model_complete':zz=zz[zz.valid_delta_fraction.ge(.95)]
    if len(zz):rows.append(dict(symbol=sym,tenor=tenor,case=case,design=design,period=period,coverage=coverage,**score(zz)))
 x=pd.read_parquet(P/f'{sym}_option_crp_trades.parquet');ctrl=x[x.design=='hold'][['day','tenor','case','integer','net','unliquidated_contracts']].rename(columns={'net':'control_net','unliquidated_contracts':'control_unliquidated'});x=x.merge(ctrl,on=['day','tenor','case','integer']);x['increment']=x.net-x.control_net
 fill=x[x.design=='fill_initial'][['day','tenor','case','integer','net','unliquidated_contracts']].rename(columns={'net':'fill_net','unliquidated_contracts':'fill_unliquidated'});x=x.merge(fill,on=['day','tenor','case','integer']);x['fill_increment']=x.net-x.fill_net
 for (tenor,case,integer,design),g in x.groupby(['tenor','case','integer','design']):
  for period,lo,hi in [('all','2021','2027'),('early','2021','2023'),('middle','2023','2025'),('recent','2025','2027')]:
   z=g[g.day.ge(lo)&g.day.lt(hi)]
   for coverage in ['all_conservative','both_liquidated']:
    zz=z if coverage=='all_conservative' else z[~z.missing_exit & z.unliquidated_contracts.eq(0)&z.control_unliquidated.eq(0)&z.fill_unliquidated.eq(0)]
    if len(zz):optrows.append(dict(symbol=sym,tenor=tenor,case=case,integer=integer,design=design,period=period,coverage=coverage,**score(zz)))
pd.DataFrame(rows).to_csv(P/'hedge_scores.csv',index=False);pd.DataFrame(optrows).to_csv(P/'option_crp_scores.csv',index=False)
print('summarized',len(rows),len(optrows))
