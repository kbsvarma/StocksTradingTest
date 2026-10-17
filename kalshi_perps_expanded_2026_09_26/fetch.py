import sys,json,time,concurrent.futures
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'))
from census import get
m=get('/margin/markets');(P/'data/markets.json').write_text(json.dumps(m));end=int(time.time())//3600*3600

def one(m):
 t=m['ticker']
 for kind,path,q in [('candles','/margin/markets/'+t+'/candlesticks',{'start_ts':1786147200,'end_ts':end,'period_interval':60}),('funding','/margin/funding_rates/historical',{'ticker':t,'start_ts':1786147200,'end_ts':end})]:
  f=P/'data'/(t+'_'+kind+'.json')
  if not f.exists():f.write_text(json.dumps(get(path,q)))
 return t
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for t in pool.map(one,m['markets']):print(t,flush=True)
(P/'data/receipt.json').write_text(json.dumps({'end_ts':end,'markets':len(m['markets']),'fetched_at':time.time()}))
