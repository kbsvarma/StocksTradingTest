"""Paced public GET receipts; never touches running processes or trading state."""
import datetime,json,time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
P=Path(__file__).resolve().parent;D=P/'receipts';D.mkdir(exist_ok=True)
ET=ZoneInfo('America/New_York');S=requests.Session();S.headers['User-Agent']='read-only-crypto-feed-audit/1.0'
def stamp(day,h,m):return int(datetime.datetime(2026,10,day,h,m,tzinfo=ET).timestamp())
last=0
def fetch(name,url,params):
 global last
 path=D/(name+'.json')
 if path.exists():return json.loads(path.read_text())
 for retry in range(4):
  time.sleep(max(0,2-(time.monotonic()-last)));last=time.monotonic();start=time.time()
  try:
   response=S.get(url,params=params,timeout=8)
   receipt=dict(url=response.url,requested=start,received=time.time(),status=response.status_code,
     headers={k:v for k,v in response.headers.items() if k.lower() in ('date','age','cache-control','cf-cache-status')},data=response.json())
   if response.status_code!=200:
    (D/(name+f'.failure{retry}.json')).write_text(json.dumps(receipt))
    response.raise_for_status()
   path.write_text(json.dumps(receipt)+'\n');return receipt
  except Exception as exc:
   if retry==3:raise
   print('retry',name,str(exc)[:120],flush=True);time.sleep(3*(retry+1))
for asset in ('BTC','ETH','SOL','XRP'):
 for day in range(3,16):
  first=stamp(day,9,54);last_bar=stamp(day,15,52)
  for k,begin in enumerate(range(first,last_bar+1,290*60)):
   fetch(f'coinbase_{asset}_{day}_{k}',f'https://api.exchange.coinbase.com/products/{asset}-USD/candles',dict(granularity=60,start=begin,end=min(begin+290*60,last_bar)))
  print('coinbase',asset,day,flush=True)
for asset,pair in [('BTC','XBTUSD'),('ETH','ETHUSD'),('SOL','SOLUSD'),('XRP','XRPUSD')]:
 r=fetch('kraken_'+asset,'https://api.kraken.com/0/public/OHLC',dict(pair=pair,interval=1,since=stamp(15,9,54)))
 print('kraken',asset,'errors',r['data'].get('error'),flush=True)
print('completed',time.time(),flush=True)
