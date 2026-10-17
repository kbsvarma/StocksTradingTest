from pathlib import Path
import os,json,csv,collections,concurrent.futures,gzip,sqlite3,zipfile
import pyarrow.parquet as pq
import pandas as pd
P=Path(__file__).resolve().parent;HOME=Path('/Users/varmakammili')
roots=[x for x in HOME.iterdir() if x.is_dir() and not x.name.startswith('.') and x.name not in {'Library','Applications','Music','Pictures','Movies','Calibre Library','Jts','IBC'}]
roots += [p for p in [HOME/'Library/CloudStorage',HOME/'Library/Mobile Documents/com~apple~CloudDocs'] if p.exists()]
roots += [p for p in Path('/Volumes').iterdir() if p.name!='Macintosh HD']
skip={'.git','node_modules','.venv','venv','__pycache__','.cache','.next','.pytest_cache','.ruff_cache','.gradle','site-packages','Pods'}
formats={'.parquet','.csv','.tsv','.feather','.arrow','.dbn','.sqlite','.sqlite3','.db','.h5','.hdf5','.zip','.gz','.zst','.xlsx','.xls','.pkl','.pickle'}
rows=[];errors=[]
for root in roots:
 print("Scanning",root,flush=True)
 for base,dirs,files in os.walk(root,onerror=lambda e:errors.append(str(e))):
  dirs[:]=[x for x in dirs if x not in skip and not x.startswith('env_')]
  if Path(base)==P:dirs[:]=[];continue
  for name in files:
   p=Path(base)/name
   if p.suffix.lower() not in formats:continue
   try:st=p.stat();rows.append(dict(path=str(p),folder=str(p.parent),extension=p.suffix.lower(),bytes=st.st_size))
   except OSError as e:errors.append(str(e))
print('Enumerated',len(rows),flush=True)
def inspect(r):
 p=Path(r['path']);ext=r['extension'];cols=[];n=None;kind='other_format';err=''
 try:
  if ext=='.parquet':
   q=pq.ParquetFile(p);cols=q.schema.names;n=q.metadata.num_rows;kind='parquet_other'
  elif ext in {'.csv','.tsv'} or p.name.endswith('.csv.gz'):
   opener=gzip.open if p.suffix=='.gz' else open
   with opener(p,'rt',errors='replace') as f:cols=next(csv.reader(f,delimiter='\t' if ext=='.tsv' else ','),[])
   kind='text_other'
  elif ext in {'.sqlite','.sqlite3','.db'}:
   con=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
   cols=[str(x) for x in con.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()];con.close();kind='database_schema'
  elif ext=='.zip':
   with zipfile.ZipFile(p) as z:cols=z.namelist()
   kind='archive_members'
  elif ext in {'.dbn','.zst'}:kind='binary_market_data_or_compressed'
  low={str(c).lower().replace(' ','_') for c in cols}
  if low&{'adj_close','adjusted_close','adjclose','adjustedclose'}:kind='adjusted_price_candidate'
  elif {'open','high','low','close'}<=low:kind='ohlc_candidate'
  elif low&{'close','price','px_close','close_price'}:kind='price_or_derived_candidate'
 except Exception as e:err=str(e)[:250];kind='inspection_error'
 return dict(**r,kind=kind,rows=n,columns=json.dumps(cols),error=err)
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
 inspected=[]
 for i,r in enumerate(pool.map(inspect,rows)):
  inspected.append(r)
  if i%20000==0:print('Inspected',i,flush=True)
f=pd.DataFrame(inspected);f.to_csv(P/'file_inventory.csv',index=False)
f.groupby(['folder','kind']).agg(files=('path','size'),bytes=('bytes','sum')).reset_index().to_csv(P/'folder_inventory.csv',index=False)
summary=dict(roots=list(map(str,roots)),excluded_runtime_dirs=sorted(skip),excluded_home_categories=['hidden application/config directories','Library except CloudStorage and Mobile Documents','Applications','Music','Pictures','Movies','Calibre Library','Jts','IBC'],files=len(f),bytes=int(f.bytes.sum()),kinds=f.kind.value_counts().to_dict(),walk_errors=errors)
(P/'inventory_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
print(f[f.kind=='adjusted_price_candidate'].groupby('folder').size().to_string())
