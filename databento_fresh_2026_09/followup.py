from common import *
from daily_designs import run
left,right='HG','SI';data={s:pd.read_parquet(D/f'daily_{s}.parquet') for s in [left,right]};ix=data[left].index.union(data[right].index);ix=ix[ix>='2018'];ds={s:data[s].reindex(ix) for s in data};rel=ds[left].ret.fillna(0)-ds[right].ret.fillna(0);score=sum(-(rel.rolling(n).sum()/rel.rolling(n).std()/np.sqrt(n)).shift().clip(-1,1).fillna(0) for n in [20,60])/2;rows=[]
for lag in [0,1]:
 for case in ['base','stress']:
  books={s:run(s,ds[s],score.shift(lag).fillna(0)*(1 if s==left else -1),'weekly',case) for s in ds}
  for group,z in {**books,'HG_SI':combine(books)}.items():
   name=f'ensemble_weekly_lag{lag}';z.to_parquet(O/f'followup_{case}_{name}_{group}.parquet')
   for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group=group,period=period,**r))
pd.DataFrame(rows).to_csv(O/'followup_scoreboard.csv',index=False)
print(pd.DataFrame(rows).query("period in ['development','validation'] and group=='HG_SI'")[['case','design','period','net','dd','sharpe']].round(3).to_string(index=False))
