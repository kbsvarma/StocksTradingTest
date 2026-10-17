from pathlib import Path
import pandas as pd,numpy as np,databento as db,json
P=Path(__file__).resolve().parent;out=P/'parsed_closes';out.mkdir(exist_ok=True)
complete=set()
for ledger in P.glob('SPEND_*.json'):
 try:complete.update(x['name'] for x in json.loads(ledger.read_text()) if x['status']=='complete')
 except json.JSONDecodeError:pass
for f in sorted((P/'vendor').glob('**/*.statistics.dbn.zst')):
 if f.parent.parent.name not in complete:continue
 name=f.parent.parent.name;dest=out/(name+'.parquet')
 if dest.exists():continue
 parts=[]
 for x in db.DBNStore.from_file(f).to_df(count=250000):
  z=x[(x.stat_type==11)&(x.update_action==1)&x.price.gt(0)].copy()
  if len(z):
   z['date']=z.ts_event.dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize();parts.append(z[['date','symbol','price','ts_event','publisher_id']].reset_index(drop=True))
 if parts:
  z=pd.concat(parts,ignore_index=True).sort_values('ts_event').drop_duplicates(['date','symbol'],keep='last');z.to_parquet(dest,index=False);print(name,len(z),z.symbol.nunique(),flush=True)
 else:pd.DataFrame(columns=['date','symbol','price','ts_event','publisher_id']).to_parquet(dest,index=False);print(name,'EMPTY',flush=True)
