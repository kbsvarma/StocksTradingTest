import sys,json,time,datetime,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'))
from census import get
for name,path,params in [('market','/margin/markets/KXGOLDPERP',None),('book','/margin/markets/KXGOLDPERP/orderbook',{'depth':20}),('risk','/margin/risk_parameters',None),('status','/margin/exchange/status',None),('hourly','/margin/markets/KXGOLDPERP/candlesticks',{'start_ts':1788220800,'end_ts':int(time.time())//3600*3600,'period_interval':60}),('funding','/margin/funding_rates/historical',{'ticker':'KXGOLDPERP','start_ts':1788220800,'end_ts':int(time.time())})]:
 try:
  r=get(path,params);(P/'data'/f'{name}.json').write_text(json.dumps(r));print(name, list(r),flush=True)
 except Exception as e:print(name,type(e).__name__,str(e)[:100],flush=True)
(P/'data'/'retrieved.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
