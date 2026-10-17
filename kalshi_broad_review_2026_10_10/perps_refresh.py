"""New period for previously specified perpetual rules. Public GETs only."""
from common import *
P0=P;D=P/'perps/data';D.mkdir(parents=True,exist_ok=True)
BEGIN=ts('2026-09-27T04:00:00Z');WARM=BEGIN-100*3600
market=get('/margin/markets',cache=False);save(D/'markets.json',market)
for m in market['markets']:
 t=m['ticker']
 for kind,path,query in [('candles','/margin/markets/'+t+'/candlesticks',{'start_ts':WARM,'end_ts':END,'period_interval':60}),('funding','/margin/funding_rates/historical',{'ticker':t,'start_ts':WARM,'end_ts':END})]:
  pathout=D/(t+'_'+kind+'.json')
  if pathout.exists():continue
  x=get(path,query)
  if x.get('cursor'):raise RuntimeError('Pagination must be implemented before analysis')
  save(pathout,x)
 print(t,flush=True)
save(D/'receipt.json',{'start_ts':BEGIN,'warm_start':WARM,'end_ts':END,'market_count':len(market['markets']),'captured':time.time()})
