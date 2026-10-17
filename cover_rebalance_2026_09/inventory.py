from pathlib import Path
import os,json,collections
import pandas as pd
import pyarrow.parquet as pq
P=Path(__file__).resolve().parent;ROOT=P.parent
roots=[Path('/Users/varmakammili/Documents/GitHub'),Path('/Users/varmakammili/Downloads')]
skip={'.git','node_modules','.venv','venv','__pycache__','.cache','.next','.pytest_cache','.ruff_cache'}
exts={'.parquet','.csv','.tsv','.feather','.arrow','.dbn','.sqlite','.sqlite3','.db','.h5','.hdf5'}
rows=[]
for root in roots:
 for base,dirs,files in os.walk(root):
  dirs[:]=[x for x in dirs if x not in skip and not x.startswith('env_')]
  for name in files:
   path=Path(base)/name
   if path.suffix.lower() not in exts and not name.endswith('.dbn.zst'):continue
   try:st=path.stat()
   except OSError:continue
   rows.append(dict(path=str(path),folder=str(path.parent),extension=path.suffix,bytes=st.st_size))
f=pd.DataFrame(rows);f.to_csv(P/'file_inventory.csv',index=False)
g=f.groupby(['folder','extension']).agg(files=('path','size'),bytes=('bytes','sum')).reset_index()
# Representative schema of each folder; actual chosen daily files audited separately.
schemas=[]
for folder in sorted(set(f.loc[f.extension=='.parquet','folder'])):
 paths=f[(f.folder==folder)&(f.extension=='.parquet')].path.sort_values().tolist()
 path=paths[len(paths)//2]
 try:
  q=pq.ParquetFile(path);names=q.schema.names
  kind='adjusted_equity' if 'Adj Close' in names else 'quotes' if 'bid_px_00' in names else 'ohlcv' if {'open','high','low','close'}<=set(names) else 'other'
  schemas.append(dict(folder=folder,sample=path,kind=kind,columns='|'.join(names),sample_rows=q.metadata.num_rows))
 except Exception as e:schemas.append(dict(folder=folder,sample=path,kind='unreadable',error=str(e)))
s=pd.DataFrame(schemas);s.to_csv(P/'folder_schemas.csv',index=False);g.to_csv(P/'folder_inventory.csv',index=False)
summary=dict(scan_roots=list(map(str,roots)),files=len(f),bytes=int(f.bytes.sum()),folders=len(g),formats=f.groupby('extension').size().to_dict(),mounted_volumes=[x.name for x in Path('/Volumes').iterdir()])
(P/'inventory_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2));print(s[s.kind=='adjusted_equity'][['folder','sample_rows']].to_string(index=False))
