from pathlib import Path
import pandas as pd,numpy as np,yfinance as yf,json,hashlib,datetime,time
from concurrent.futures import ThreadPoolExecutor,as_completed
P=Path(__file__).resolve().parent;(P/'data').mkdir(exist_ok=True)
U=json.loads((P/'universe.json').read_text()); lookup={s:c for c,ss in U.items() for s in ss}
assert len(lookup)==sum(map(len,U.values()))
def get(s):
 f=P/'data'/f'{s}.csv';error=None
 for attempt in range(3):
  try:
   if f.exists():d=pd.read_csv(f,index_col=0,parse_dates=True)
   else:
    d=yf.Ticker(s).history(start='2014-01-01',end='2026-09-26',auto_adjust=False,actions=True,raise_errors=True)
    assert len(d)>20,'Too little data'
    d.index=pd.to_datetime(d.index.date);d.to_csv(f,index_label='Date')
   assert d.index.max()<=pd.Timestamp('2026-09-25') and not d.index.duplicated().any()
   return dict(ticker=s,category=lookup[s],rows=len(d),first=str(d.index.min().date()),last=str(d.index.max().date()),events=int((d.Dividends>0).sum()),sha256=hashlib.sha256(f.read_bytes()).hexdigest(),error=None)
  except Exception as e:error=str(e);time.sleep(1)
 return dict(ticker=s,category=lookup[s],error=error)
results=[]
with ThreadPoolExecutor(max_workers=4) as ex:
 for f in as_completed([ex.submit(get,s) for s in lookup]):
  row=f.result();results.append(row);print(json.dumps(row),flush=True)
results.sort(key=lambda x:x['ticker']);pd.DataFrame(results).to_csv(P/'coverage.csv',index=False)
(P/'manifest.json').write_text(json.dumps(dict(source='Yahoo Finance via yfinance',version=yf.__version__,retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=results),indent=2))
