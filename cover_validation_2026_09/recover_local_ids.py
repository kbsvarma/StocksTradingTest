from pathlib import Path
import pandas as pd,numpy as np,json,time
from db_access import client,safe_error
P=Path(__file__).resolve().parent;source=P.parent/'opening_leader_2026_07/data';out=P/'recovered_maps';out.mkdir(exist_ok=True)
for y in range(2018,2027):
 d=pd.read_parquet(source/f'daily_{y}.parquet');ids=sorted(map(int,d.instrument_id.unique()));start=max(f'{y}-01-01','2018-05-01');end=min(f'{y+1}-01-01','2026-09-12')
 for k in range(0,len(ids),1000):
  f=out/f'{y}_{k:05d}.parquet'
  if f.exists():continue
  try:
   r=client.symbology.resolve(dataset='XNAS.ITCH',symbols=ids[k:k+1000],stype_in='instrument_id',stype_out='raw_symbol',start_date=start,end_date=end);rows=[{'instrument_id':int(i),'start':v['d0'],'end':v['d1'],'symbol':v['s']} for i,vs in r['result'].items() for v in vs];pd.DataFrame(rows,columns=['instrument_id','start','end','symbol']).to_parquet(f,index=False);print(y,k,len(rows),flush=True)
  except Exception as e:(out/f'{y}_{k:05d}_error.json').write_text(json.dumps({'error':safe_error(e)}));print('ERROR',y,k,safe_error(e),flush=True)
 print('YEAR DONE',y,flush=True)
print('DONE',flush=True)
