from pathlib import Path
import pandas as pd,numpy as np,json,concurrent.futures,hashlib,logging,threading,time
import yfinance as yf
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
P=Path(__file__).resolve().parent;R=P.parent;D=P/'supplemental_prices';D.mkdir(exist_ok=True)
panel=pd.read_parquet(R/'volatile_direction_2026_07/data/pit_panel_2018_2026.parquet',columns=['raw_symbol','day','security_type'])
stock=panel[panel.security_type.isin(['C','A'])].groupby('raw_symbol').agg(local_rows=('day','size'),local_start=('day','min'),local_end=('day','max')).reset_index()
stock['symbol']=stock.raw_symbol.replace({'FB':'META','BRK-B':'BRK.B'})
stock.to_csv(P/'local_mapped_stock_universe.csv',index=False)
names=sorted(stock.symbol.unique());stop=threading.Event()
def fetch(n):
 cached=R/f'stock_overnight_2026_09/data/{n}.parquet';target=D/f'{n}.parquet'
 base=dict(symbol=n,source='existing_cache' if cached.exists() else 'supplemental_yahoo',path=str(cached if cached.exists() else target))
 if stop.is_set() and not cached.exists():return dict(**base,status='deferred_after_rate_limit')
 try:
  if cached.exists():d=pd.read_parquet(cached)
  elif target.exists():d=pd.read_parquet(target)
  else:
   ticker=yf.Ticker(n.replace('.','-'))
   d=ticker.history(start='2017-01-01',end='2026-09-12',auto_adjust=False,actions=True,raise_errors=True,timeout=15)
   if not d.empty:d.to_parquet(target)
  if d.empty:return dict(**base,status='empty')
  return dict(**base,status='ok',rows=len(d),start=str(d.index.min()),end=str(d.index.max()),sha256=hashlib.sha256(Path(base['path']).read_bytes()).hexdigest())
 except Exception as ex:
  msg=str(ex)[:220]
  if 'rate' in msg.lower() and 'limit' in msg.lower():stop.set()
  return dict(**base,status='failed',error=msg)
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
 for r in pool.map(fetch,names):
  rows.append(r)
  if len(rows)%50==0:
   pd.DataFrame(rows).to_csv(P/'source_manifest.csv',index=False);print('processed',len(rows),'of',len(names),'ok',sum(x['status']=='ok' for x in rows),'rate_limit_stop',stop.is_set(),flush=True)
pd.DataFrame(rows).to_csv(P/'source_manifest.csv',index=False);print('DONE',pd.DataFrame(rows).status.value_counts().to_dict(),flush=True)
