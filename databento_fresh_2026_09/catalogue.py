from pathlib import Path
import json,collections,hashlib
import pyarrow.parquet as pq
P=Path(__file__).resolve().parent;ROOT=P.parent;items=json.loads((P/'file_inventory.json').read_text());rows=[];errors=[]
for i,a in enumerate(items):
 p=ROOT/a['path'];row=dict(a)
 if a['format']=='.parquet':
  try:
   f=pq.ParquetFile(p);names=f.schema.names;row['rows']=f.metadata.num_rows;row['columns']='|'.join(names);row['kind']='ohlcv' if all(k in names for k in ['open','high','low','close']) else ('quotes' if 'bid_px_00' in names else ('statistics' if 'stat_type' in names else ('definitions' if 'raw_symbol' in names and 'security_type' in names else 'derived_or_other')))
   times=[c for c in ['ts_recv','ts_event','timestamp','__index_level_0__','date','day'] if c in names];row['time_column']=times[0] if times else '';mi=[];ma=[]
   if times:
    ci=names.index(times[0])
    for j in range(f.metadata.num_row_groups):
     st=f.metadata.row_group(j).column(ci).statistics
     if st and st.has_min_max:mi.append(st.min);ma.append(st.max)
   row['start']=str(min(mi)) if mi else '';row['end']=str(max(ma)) if ma else ''
  except Exception as e:row['kind']='unreadable';errors.append(dict(path=a['path'],error=str(e)))
 elif a['format']=='dbn':row['kind']='raw_dbn'
 elif a['format']=='metadata':
  try:
   q=json.loads(p.read_text());q=q.get('query',q);row.update({k:str(q.get(k,'')) for k in ['dataset','schema','start','end','symbols','stype_in']});row['kind']='request_metadata'
  except Exception as e:row['kind']='unreadable';errors.append(dict(path=a['path'],error=str(e)))
 else:row['kind']='other'
 rows.append(row)
 if i%10000==0:print('Catalogued',i,flush=True)
import pandas as pd
f=pd.DataFrame(rows);f.to_csv(P/'catalogue.csv',index=False);(P/'catalogue_errors.json').write_text(json.dumps(errors,indent=2));s=f.groupby('kind').agg(files=('path','size'),bytes=('bytes','sum'),records=('rows','sum'));s.to_csv(P/'catalogue_summary.csv');print(s.to_string(),flush=True)
# Summarize data families without treating copies or result paths as independent data.
f['folder']=f.path.map(lambda x:str(Path(x).parent));g=f.groupby(['folder','kind']).agg(files=('path','size'),bytes=('bytes','sum'),records=('rows','sum'),start=('start','min'),end=('end','max'));g.to_csv(P/'coverage_by_folder.csv')
