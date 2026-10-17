"""GET-only exchange proof and archived candles for unrecovered non-WTI cases."""
import gzip,json,sys,time,urllib.parse
from pathlib import Path
import requests
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
def load(n):return json.load(gzip.open(P/(n+'.json.gz'),'rt'))
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
orders=load('orders');attempts=load('attempts');zero=[r for r in attempts if r['status']=='unfilled']
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6);original=client._request;log=[]
def readonly(method,path,**kw):
 assert method=='GET','Only GET allowed'
 start=time.time()
 try:return original(method,path,**kw)
 finally:log.append(dict(path=path,received=time.time(),elapsed=time.time()-start));time.sleep(.2)
client._request=readonly;ex=Exchange(client);proof={}
for r in zero:
 t=r['ticker']
 try:proof[t]=dict(order=ex.order(r['order_id']),fills=ex.fills(r['order_id']),market_orders=ex.orders(ticker=t))
 except Exception as e:proof[t]=dict(error=str(e)[:300])
save('exchange_proof.json',proof);client.session.close()
session=requests.Session();session.headers['User-Agent']='kalshi-lane-lockout-readonly-audit/1.0'
def public(url):
 second=time.time()%60
 if second<33:time.sleep(33-second)
 if second>57:time.sleep(93-second)
 t=time.time();r=session.get(url,timeout=8);r.raise_for_status()
 log.append(dict(url=url,received=time.time(),elapsed=time.time()-t));time.sleep(.3)
 return r.json()
base='https://api.elections.kalshi.com/trade-api/v2'
archive={}
for r in orders:
 if r['status']!='unfilled' or r['asset'] not in ('BTC','ETH','SOL','XRP'):continue
 t=r['ticker'];start=r['close_ts']-900;T=r['signal']['decision_ts']
 if T+60>r['close_ts']-180:continue # No further minute inside unchanged 3–10min gate.
 try:
  market=public(base+'/markets/'+t)['market']
  candles=public(base+'/series/KX'+r['asset']+'15M/markets/'+t+'/candlesticks?'+urllib.parse.urlencode(dict(start_ts=start,end_ts=r['close_ts'],period_interval=1)))
  spot=public('https://api.exchange.coinbase.com/products/'+r['asset']+'-USD/candles?'+urllib.parse.urlencode(dict(granularity=60,start=T-100*60,end=r['close_ts'])))
  archive[t]=dict(market=market,candles=candles,spot=spot,received_ts=time.time(),first_arrival_evidence=False)
 except Exception as e:archive[t]=dict(error=str(e)[:300])
 save('archive.json',archive)
save('request_log.json',log)
print(json.dumps(dict(exchange_zero_cases=len(proof),exchange_errors={t:p['error'] for t,p in proof.items() if 'error' in p},archived={t:dict(error=a.get('error'),candles=len(a.get('candles',{}).get('candlesticks',[])),spot_bars=len(a.get('spot',[]))) for t,a in archive.items()}),indent=2))
