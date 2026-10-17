from common import *
from daily_designs import run
rows=[]
for sign,name in [(1,'constant_HG_long_SI_short'),(-1,'constant_HG_short_SI_long')]:
 for case in ['base','stress']:
  books={}
  for s in ['HG','SI']:
   d=pd.read_parquet(D/f'daily_{s}.parquet');d=d[d.index>='2018'];books[s]=run(s,d,pd.Series(sign*(1 if s=='HG' else -1),index=d.index),'weekly',case)
  z=pd.concat(books).groupby(level=1).sum();z.to_parquet(O/f'control_{case}_{name}.parquet')
  for period,r in partitions(z).items():rows.append(dict(case=case,design=name,group='HG_SI',period=period,**r))
pd.DataFrame(rows).to_csv(O/'control_scoreboard.csv',index=False)
print(pd.DataFrame(rows).query("case=='base'")[['design','period','net','sharpe']].round(3).to_string(index=False))
contributions=[]
for s in ['DX','CT','KC','SB','CC','BRN']:
 a=(pd.read_parquet(O/f'ice_momentum20_base_{s}.parquet')+pd.read_parquet(O/f'ice_momentum60_base_{s}.parquet'))/12;a=a[(a.index>='2020')&(a.index<='2026-06-02')]
 for year,net in a.net.groupby(a.index.year).sum().items():contributions.append(dict(symbol=s,year=year,hybrid_pnl=net))
pd.DataFrame(contributions).to_csv(O/'hybrid_ice_contributions.csv',index=False)
print(pd.DataFrame(contributions).pivot(index='symbol',columns='year',values='hybrid_pnl').round(1).to_string())
