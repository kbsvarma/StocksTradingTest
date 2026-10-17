from pathlib import Path
import pandas as pd,yfinance as yf,json,concurrent.futures
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(exist_ok=True)
u=pd.read_parquet(P.parent/'opening_leader_2026_07/data/universe.parquet');names=sorted(set(u.symbol)|{'SPY'});aliases={'FB':'META','BRK.B':'BRK-B'}
def get(symbol):
 target=aliases.get(symbol,symbol);f=D/f'{symbol if symbol == "BRK.B" else target}.parquet'
 if f.exists():
  b=pd.read_parquet(f)
 else:
  try:
   b=yf.Ticker(target).history(start='2017-01-01',end='2026-09-12',auto_adjust=False,actions=True,raise_errors=True)
   if len(b):b.to_parquet(f)
  except Exception as e:return dict(symbol=symbol,target=target,status='failed',error=str(e)[:180])
 if not len(b):return dict(symbol=symbol,target=target,status='empty')
 return dict(symbol=symbol,target=target,status='ok',rows=len(b),start=str(b.index.min()),end=str(b.index.max()))
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
 for r in pool.map(get,names):
  rows.append(r)
  if len(rows)%25==0:
   (P/'download_manifest.json').write_text(json.dumps(rows,indent=2));print('downloaded',len(rows),'ok',sum(x['status']=='ok' for x in rows),flush=True)
(P/'download_manifest.json').write_text(json.dumps(rows,indent=2));print('finished',len(rows),'ok',sum(x['status']=='ok' for x in rows),flush=True)
