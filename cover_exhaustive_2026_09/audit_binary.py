from pathlib import Path
import pandas as pd,databento as db,concurrent.futures,json
P=Path(__file__).resolve().parent;f=pd.read_csv(P/'file_inventory.csv');f=f[f.kind=='binary_market_data_or_compressed']
def read(path):
 try:
  m=db.DBNStore.from_file(path).metadata
  return dict(path=path,dataset=m.dataset,schema=str(m.schema),symbols=json.dumps(m.symbols),status='metadata_read')
 except Exception as e:return dict(path=path,status='unreadable_or_not_dbn',error=str(e)[:120])
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
 rows=list(ex.map(read,f.path))
r=pd.DataFrame(rows);r.to_csv(P/'binary_metadata.csv',index=False);print(r.groupby(['dataset','schema']).size().to_string());print(r.status.value_counts().to_string())
