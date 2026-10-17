from pathlib import Path
import pandas as pd,numpy as np,json
from db_access import download,client
P=Path(__file__).resolve().parent
known=set()
for f in P.glob('definition_20*.parquet'):
 d=pd.read_parquet(f,columns=['raw_symbol']);known.update(d.raw_symbol)
first=[]
for f in (P/'mapped_local').glob('*.parquet'):
 d=pd.read_parquet(f,columns=['symbol','day']);first.append(d.groupby('symbol').day.min())
s=pd.concat(first).groupby(level=0).min();s=s[~s.index.isin(known)];plans=[]
for month,z in s.groupby(s.dt.to_period('M')):
 lo=max(str(month.start_time.date()),'2018-05-01');hi=min(str((month+1).start_time.date()),'2026-09-12');symbols=sorted(z.index)
 for off in range(0,len(symbols),500):
  params=dict(dataset='XNAS.ITCH',symbols=symbols[off:off+500],schema='definition',start=lo,end=hi);name='definition_missing_'+str(month)+'_'+str(off);plans.append((name,params))
print('unclassified',len(s),'requests',len(plans),flush=True)
for name,params in plans:
 f=P/(name+'.parquet')
 if f.exists():continue
 try:
  x=download(name,params,ledger_name='SPEND_CLASSIFICATION.json').to_df();x[['raw_symbol','security_type','instrument_id']].drop_duplicates().to_parquet(f,index=False)
 except Exception as e:print('FAILED',name,str(e)[:300],flush=True)
allrows=[]
for f in P.glob('definition_*.parquet'):
 d=pd.read_parquet(f,columns=['raw_symbol','security_type']);allrows.append(d)
d=pd.concat(allrows).drop_duplicates();d.to_csv(P/'historical_security_types.csv',index=False);stocks=set(d.loc[d.security_type.isin(['C','A']),'raw_symbol']);prior=set(json.loads((P/'expanded_symbols.json').read_text()));new=sorted(stocks-prior);(P/'completion_new_symbols.json').write_text(json.dumps(new));print('DONE new common ADR',len(new),flush=True)
