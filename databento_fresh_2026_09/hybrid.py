from common import *
rows=[]
for case in ['base','stress']:
 a=pd.read_parquet(O/f'ice_momentum20_{case}_all.parquet');b=pd.read_parquet(O/f'ice_momentum60_{case}_all.parquet');ice=(a+b)/2;pair=pd.read_parquet(O/f'followup_{case}_ensemble_weekly_lag0_HG_SI.parquet');ix=ice.index.union(pair.index);blend=ice.reindex(ix,fill_value=0)/6+pair.reindex(ix,fill_value=0)/2
 # Two nominal ATR risk units in each displayed portfolio.
 for label,z in [('ice_ensemble',ice/3),('hybrid',blend)]:
  z=z[(z.index>='2020')&(z.index<='2026-06-02')];z.to_parquet(O/f'hybrid_{case}_{label}.parquet')
  for period,r in partitions(z).items():rows.append(dict(case=case,design=label,group='portfolio',period=period,**r))
pd.DataFrame(rows).to_csv(O/'hybrid_scoreboard.csv',index=False)
print(pd.DataFrame(rows)[['case','design','period','net','dd','sharpe','worst_year']].round(3).to_string(index=False))
