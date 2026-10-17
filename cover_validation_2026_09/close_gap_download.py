from pathlib import Path
import pandas as pd
from db_access import download
P=Path(__file__).resolve().parent
for dataset,symbols in [('XASE.PILLAR',['LEU','LNG','GSAT']),('XNYS.PILLAR',['BRK/B','BRK B'])]:
 name='gap_'+dataset.split('.')[0];data=download(name,dict(dataset=dataset,symbols=symbols,schema='statistics',start='2018-05-01',end='2026-09-12'),ledger_name='SPEND_GAPS.json');x=data.to_df();z=x[(x.stat_type==11)&(x.update_action==1)&x.price.gt(0)].copy();z['date']=z.ts_event.dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize();z=z[['date','symbol','price','ts_event','publisher_id']].reset_index(drop=True);z.to_parquet(P/'parsed_closes'/(name+'.parquet'),index=False);print(name,len(z),flush=True)
