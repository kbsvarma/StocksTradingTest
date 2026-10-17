from pathlib import Path
import pandas as pd,json,concurrent.futures,threading,hashlib,time,logging,warnings
import yfinance as yf
warnings.filterwarnings('ignore');logging.getLogger('yfinance').setLevel(logging.CRITICAL)
P=Path(__file__).resolve().parent;R=P.parent;m=pd.read_csv(P/'source_manifest.csv');allnames=set(json.loads((P/'all_local_symbols.json').read_text()));done=set(m[m.status=='ok'].symbol)
pending=set(m[m.status=='deferred_after_rate_limit'].symbol)|set(m[m.error.fillna('').str.contains('Rate limited')].symbol)|(allnames-set(m.symbol))
stop=threading.Event()
def get(n):
 path=P/f'supplemental_prices/{n}.parquet';cached=R/f'stock_overnight_2026_09/data/{n}.parquet'
 base=dict(symbol=n,source='existing_cache' if cached.exists() else 'supplemental_yahoo',path=str(cached if cached.exists() else path))
 if stop.is_set():return dict(**base,status='deferred_after_rate_limit')
 try:
  if cached.exists():d=pd.read_parquet(cached)
  elif path.exists():d=pd.read_parquet(path)
  else:
   time.sleep(.2);d=yf.Ticker(n.replace('.','-')).history(start='2017-01-01',end='2026-09-12',auto_adjust=False,actions=True,raise_errors=True,timeout=15)
   if len(d):d.to_parquet(path)
  if d.empty:return dict(**base,status='empty')
  return dict(**base,status='ok',rows=len(d),start=str(d.index.min()),end=str(d.index.max()),sha256=hashlib.sha256(Path(base['path']).read_bytes()).hexdigest())
 except Exception as e:
  msg=str(e)[:220]
  if 'rate' in msg.lower() and 'limit' in msg.lower():stop.set()
  return dict(**base,status='failed',error=msg)
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(get,sorted(pending)):
  rows.append(r)
  if len(rows)%50==0:
   pd.DataFrame(rows).to_csv(P/'supplement_pass2.csv',index=False);print(len(rows),'/',len(pending),'ok',sum(x['status']=='ok' for x in rows),'rate_stop',stop.is_set(),flush=True)
n=pd.DataFrame(rows);n.to_csv(P/'supplement_pass2.csv',index=False);combined=pd.concat([m[~m.symbol.isin(n.symbol)],n],ignore_index=True);combined.to_csv(P/'source_manifest.csv',index=False);print('DONE',combined.status.value_counts().to_dict(),flush=True)
