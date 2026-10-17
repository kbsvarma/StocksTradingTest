from pathlib import Path
import pandas as pd,numpy as np,statsmodels.api as sm
P=Path(__file__).resolve().parent;d=pd.read_csv(P/'daily.csv',parse_dates=['entry','exit']);p=d.pivot(index='entry',columns='strategy',values='gross');rows=[]
for period,start in [('All','2018-01-01'),('2022+','2022-01-01'),('2025+','2025-01-01')]:
 q=p.loc[start:]
 for s in ['OBG','TopDecile']:
  for cost in [3,10]:
   y=q[s]-cost/1e4;X=sm.add_constant(q[['Universe','SPYbuyhold']]);fit=sm.OLS(y,X).fit(cov_type='HAC',cov_kwds={'maxlags':20});ci=fit.conf_int().loc['const']
   rows.append(dict(period=period,strategy=s,cost_bps=cost,alpha_bps_per_day=fit.params['const']*1e4,low=ci.iloc[0]*1e4,high=ci.iloc[1]*1e4,beta_universe=fit.params['Universe'],beta_spy_full_day=fit.params['SPYbuyhold']))
pd.DataFrame(rows).to_csv(P/'risk_adjusted.csv',index=False)
# Terminal-value bracket; optimistic cash timing, NOT a next-open fill replay.
t=pd.read_parquet(P/'trades.parquet');s=d[d.strategy=='TopDecile'].copy();r=s.gross-s.weight*.0003
bad=t[(t.strategy=='TopDecile')&t.missing_exit]
assert set(bad.symbol)=={'TSRO'}
b=pd.read_parquet(P.parent/'data/TSRO.parquet');b.index=pd.to_datetime(b.index.date)
for row in bad.itertuples():
 ix=s.index[s.entry==row.entry];r.loc[ix]+=row.weight*(1+75/b.loc[row.entry,'Close']-1)
base=s.gross-s.weight*.0003
pd.DataFrame([dict(case='missing_exit_full_loss_stress',cagr=np.expm1(np.log1p(base).mean()*252)*100),dict(case='TSRO_75_terminal_value_optimistic_timing',cagr=np.expm1(np.log1p(r).mean()*252)*100)]).to_csv(P/'merger_bracket.csv',index=False)
# Independent return and normalization verification; residual archive-vendor mismatches.
t=pd.read_parquet(P/'execution_trades.parquet');z=t[(t.strategy=='Universe')&t.m0930.notna()];ratio=z.m0930/(z.factor*z.dailyopen);bad=z[(ratio<.8)|(ratio>1.2)]
bad.to_csv(P/'identity_audit_after.csv',index=False)
for row in t[t.m0935.notna()].sample(500,random_state=32).itertuples():
 assert abs(((row.m0935/row.factor+row.div)/row.entry_close-1)-row.m0935_ret)<1e-12
assert len(bad)==0
assert not t.duplicated(['entry','symbol','strategy']).any()
assert not t[(t.symbol=='CZR')&(t.entry<'2020-07-21')].shape[0]
assert not t[(t.symbol=='MTCH')&(t.entry<'2020-07-01')].shape[0]
print('500 normalization/replay checks passed; zero >20% archive-vendor opening mismatches remain in Universe matched pairs.')
print(pd.DataFrame(rows).to_string(index=False))
