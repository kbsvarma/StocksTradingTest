from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent
tr=pd.read_parquet(P/'trades.parquet');tr=tr[(tr.entry>='2026-05-20')&(tr.exit<='2026-07-02')&(tr.strategy.isin(['OBG','TopDecile']))]
cache={};out=[]
for s in tr.symbol.unique():
 f=R/'shares_research/open_fade_2026_07_03/pull_cache'/f'{s}.parquet'
 if not f.exists():continue
 b=pd.read_parquet(f);b.index=b.index.tz_convert('America/New_York');cache[s]=b
for r in tr.itertuples():
 if r.symbol not in cache:continue
 b=cache[r.symbol];en=r.entry.tz_localize('America/New_York');ex=r.exit.tz_localize('America/New_York')
 try:
  entry=b.loc[en+pd.Timedelta(hours=15,minutes=55),'Open'];exit=b.loc[ex+pd.Timedelta(hours=9,minutes=35),'Open']
  raw=pd.read_parquet(P/'data'/f'{r.symbol}.parquet');raw.index=pd.to_datetime(raw.index.date)
  div=raw.loc[r.exit,'Dividends'];dailyclose=raw.loc[r.entry,'Close']
  out.append(dict(entry=r.entry,strategy=r.strategy,symbol=r.symbol,weight=r.weight,official=r.ret,delayed_exit=(exit+div)/dailyclose-1,both_off_auction=(exit+div)/entry-1))
 except (KeyError,FileNotFoundError):continue
z=pd.DataFrame(out);z.to_csv(P/'execution_pairs.csv',index=False)
for s,g in z.groupby('strategy'):
 expected=tr[tr.strategy==s];print(s,'pairs',len(g),'of',len(expected),'weight coverage',g.weight.sum()/expected.weight.sum())
 for k in ['official','delayed_exit','both_off_auction']:
  print(k,'matched weighted mean bps',np.average(g[k],weights=g.weight)*10000)
