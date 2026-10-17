from pathlib import Path
import pandas as pd,json
from db_access import download
P=Path(__file__).resolve().parent;f=P/'issuer_alias_intervals.json';a=json.loads(f.read_text());a['HIMS']=[['OAC','2018-05-01','2021-01-21'],['HIMS','2021-01-21','2026-09-12']];a['ACHR']=[['ACIC','2018-05-01','2021-09-17'],['ACHR','2021-09-17','2026-09-12']];f.write_text(json.dumps(a,indent=2));status=[]
for symbol,end in [('OAC','2021-01-21'),('ACIC','2021-09-17')]:
 for schema in ['bbo-1m','status']:
  name=symbol.lower()+'_'+schema.replace('-','_');data=download(name,dict(dataset='XNAS.ITCH',symbols=[symbol],schema=schema,start='2018-05-01',end=end),ledger_name='SPEND_SPAC_LINEAGE.json');x=data.to_df()
  if schema=='status':status.append(x);continue
  t=x.index.tz_convert('America/New_York');mins=t.hour*60+t.minute;keep=mins.isin([777,778,779,780,957,958,959,960]);z=x.loc[keep].copy();z['date']=t[keep].tz_localize(None).normalize();z['minute']=mins[keep];z.reset_index().to_parquet(P/('execution_snapshots_'+name+'.parquet'),index=False)
pd.concat(status).to_parquet(P/'trading_status_spac.parquet');print('DONE',flush=True)
