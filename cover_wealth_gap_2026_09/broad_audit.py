from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';O=P.parent/'cover_exhaustive_2026_09';reg=pd.read_csv(O/'stock_coverage.csv').set_index('symbol');q=pd.read_parquet(P/'broad_local_prices.parquet');types=pd.read_csv(V/'historical_security_types.csv').drop_duplicates('raw_symbol').set_index('raw_symbol').security_type
prof=pd.read_csv(V/'company_quality_audit.csv').set_index('ticker');ex=set(pd.read_csv(V/'ai_exclusions_final.csv').symbol)
def audit(s):
 f=V/'expanded_daily'/f'{s}.parquet'
 if not f.exists():
  if s not in reg.index or not isinstance(reg.loc[s,'path'],str):return dict(symbol=s,status='missing_source')
  f=Path(reg.loc[s,'path'])
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);d=d.reindex(q.index);z=d['Adj Close'].notna()&d['Adj Close'].gt(0);bad=np.flatnonzero(~z);start=bad[-1]+1 if len(bad) else 0;d=d.iloc[start:];dv=d.Close*d.Volume;r=d['Adj Close']/d['Adj Close'].shift();kind=types.get(s,'unknown');known=s in prof.index and bool(prof.loc[s,'company_stock']);quality=(d.Volume.le(0).mean()<=.01)&(dv.quantile(.1)>=1e5)&(~((r>4)|(r<.25))).all()
 return dict(symbol=s,status='read',security_type=kind,company_known=known or kind in ['C','A'],no_ai=s not in ex,quality_proxy=quality,p10_dollar_volume=dv.quantile(.1),zero_volume_fraction=d.Volume.le(0).mean(),extreme_4x=((r>4)|(r<.25)).sum())
with ThreadPoolExecutor(8) as pool:a=pd.DataFrame(pool.map(audit,q.columns))
a.to_csv(P/'broad_symbol_audit.csv',index=False);a=a.set_index('symbol');x=pd.read_parquet(P/'broad_all_pairs.parquet');x['admissible']=True;x['no_ai']=True
for side in ['a','b']:x.admissible&=x[side].map(a.quality_proxy).fillna(False)&x[side].map(a.company_known).fillna(False);x.no_ai&=x[side].map(a.no_ai).fillna(False)
x.to_parquet(P/'broad_audited_pairs.parquet',index=False);z=x[x.admissible&x.new_pair&x.rebalance_multiple.ge(1)];z.head(100).to_csv(P/'broad_audited_new_top100.csv',index=False);print('new candidates passing source/type proxies',len(z),flush=True);print(z.head(20).to_string(index=False),flush=True)
