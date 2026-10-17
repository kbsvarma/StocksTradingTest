"""Bounded, resumable market-data GETs. No trade/account mutations."""
import sys,json,gzip,time,hashlib,threading,datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
P=Path(__file__).resolve().parent;DATA=P/'data';DATA.mkdir(exist_ok=True)
START=int(dt.datetime(2026,9,12,4,tzinfo=dt.timezone.utc).timestamp())
END=int(dt.datetime(2026,10,10,4,tzinfo=dt.timezone.utc).timestamp())
ETHSTART=int(dt.datetime(2026,10,1,4,tzinfo=dt.timezone.utc).timestamp())
BASE='https://external-api.kalshi.com/trade-api/v2'
lock=threading.Lock();next_request=0.;local=threading.local()
def ts(x):return int(dt.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp())
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
 tmp.write_text(json.dumps(x,separators=(',',':'))+'\n');tmp.replace(p)
def get(path,params=None,base=BASE):
 global next_request
 if not hasattr(local,'s'):local.s=requests.Session()
 for attempt in range(5):
  with lock:
   wait=max(0,next_request-time.monotonic());next_request=max(next_request,time.monotonic())+.4
  time.sleep(wait)
  try:
   sent=time.time();r=local.s.get(base+path,params=params,timeout=35);r.raise_for_status()
   return dict(sent_ts=sent,received_ts=time.time(),url=r.url,sha256=hashlib.sha256(r.content).hexdigest(),response=r.json())
  except Exception:
   if attempt==4:raise
   time.sleep(2**attempt)
def compact(m):
 keys=('ticker','event_ticker','open_time','close_time','settlement_ts','result','floor_strike','cap_strike','strike_type','volume_fp','open_interest_fp','expiration_value','notional_value_dollars','price_ranges','exchange_index','custom_strike')
 return {k:m.get(k) for k in keys}
def markets(series,start):
 dest=DATA/series;dest.mkdir(exist_ok=True);path=dest/'markets.json'
 if path.exists():return json.loads(path.read_text())
 save(dest/'series.json',get('/series/'+series));rows={};cursor='';page=0
 while True:
  raw=get('/events',dict(series_ticker=series,status='settled',limit=200,with_nested_markets='true',**({'cursor':cursor} if cursor else {})))
  with gzip.open(dest/f'event_page_{page}.json.gz','wt') as f:json.dump(raw,f,separators=(',',':'))
  events=raw['response'].get('events',[]);oldest=END;included=0
  for e in events:
   if e.get('strike_date'):oldest=min(oldest,ts(e['strike_date']))
   for m in e.get('markets',[]):
    c=ts(m['close_time']);oldest=min(oldest,c)
    if start<=c<END:rows[m['ticker']]=compact(m);included+=1
  print(series,'page',page,'kept',len(rows),flush=True)
  cursor=raw['response'].get('cursor');page+=1
  if not events or not cursor or oldest<start:break
 result=sorted(rows.values(),key=lambda m:(m['close_time'],m['ticker']));save(path,result);return result
def candles(series,rows):
 dest=DATA/series;path=dest/'candles.jsonl';done=set()
 if path.exists():done={json.loads(l)['ticker'] for l in path.open()}
 eligible=[m for m in rows if m['ticker'] not in done and m['result'] in ('yes','no') and float(m['volume_fp'] or 0)>0]
 groups=[];group=[]
 for m in eligible:
  trial=group+[m];span=(ts(trial[-1]['close_time'])-ts(trial[0]['close_time']))//60+34
  if group and (len(trial)>100 or len(trial)*span>8000):groups.append(group);group=[]
  group.append(m)
 if group:groups.append(group)
 print(series,'quote batches',len(groups),'markets',len(eligible),flush=True)
 for i,g in enumerate(groups):
  raw=get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in g),start_ts=ts(g[0]['close_time'])-32*60,end_ts=ts(g[-1]['close_time']),period_interval=1))
  with gzip.open(dest/('quotes_'+g[0]['ticker']+'.json.gz'),'wt') as f:json.dump(raw,f,separators=(',',':'))
  by={m['market_ticker']:m['candlesticks'] for m in raw['response']['markets']}
  with path.open('a') as f:
   for m in g:
    cs=[]
    for c in by.get(m['ticker'],[]):
     if not ts(m['close_time'])-32*60<=c['end_period_ts']<=ts(m['close_time']):continue
     x=dict(t=c['end_period_ts'],v=c.get('volume_fp'))
     for field,prefix in [('yes_bid','b'),('yes_ask','a')]:
      for phase,suf in [('open','o'),('close','c')]:x[prefix+suf]=c.get(field,{}).get(phase+'_dollars')
     cs.append(x)
    f.write(json.dumps(dict(ticker=m['ticker'],candles=cs),separators=(',',':'))+'\n')
  if i%25==0:print(series,'quotes',i+1,'/',len(groups),flush=True)
def one(series):
 rows=markets(series,ETHSTART if series=='KXETHD' else START);candles(series,rows)
 return dict(series=series,markets=len(rows),events=len({m['event_ticker'] for m in rows}),positive_volume=sum(float(m['volume_fp'] or 0)>0 for m in rows))
def benchmark():
 sys.path.insert(0,str(P.parent/'kalshi_revalidation_2026_09_12/live_v1'))
 from kalshi_client import Credentials,KalshiClient
 class ReadOnly(KalshiClient):
  def _request(self,method,path,**kwargs):
   assert method=='GET' and path=='/cfbenchmarks/history/values'
   return super()._request(method,path,**kwargs)
 path=DATA/'benchmark.jsonl'
 if not path.exists():
  source=P.parent/'kalshi_btc_eth_quant_2026_10_02/fresh_data/benchmark.jsonl'
  with path.open('w') as f:
   for l in source.open():
    x=json.loads(l)
    if START-7200<=x['hour']<END:f.write(l)
 done={(x['asset'],x['hour']) for x in map(json.loads,path.read_text().splitlines())}
 client=ReadOnly(creds=Credentials.load(),dry_run=True)
 jobs=[(a,ident,h) for a,ident,lo in [('BTC','BRTI',START),('ETH','ETHUSD_RTI',ETHSTART)] for h in range(lo-7200,END,3600) if (a,h) not in done]
 print('CF missing hours',len(jobs),flush=True)
 for i,(a,ident,h) in enumerate(jobs):
  before=time.monotonic()
  raw=client._request('GET','/cfbenchmarks/history/values',params={'id':ident,'timespan':'HOUR','timestamp':dt.datetime.fromtimestamp(h,dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')})
  d=raw.get('data',{});assert not d.get('error'),str(d.get('error'))
  seconds={int(v['time'])//1000:float(v['value']) for v in d.get('payload',[]) if int(v['time'])%1000==0 and h<=int(v['time'])//1000<h+3600}
  minutes=[]
  for t in range(h,h+3600,60):
   vals=[seconds[s] for s in range(t,t+60) if s in seconds]
   if len(vals)==60:minutes.append([t,vals[-1],sum(vals)/60])
  assert minutes,'No complete CF minutes'
  dest=DATA/'cf_raw';dest.mkdir(exist_ok=True)
  with gzip.open(dest/f'{a}_{h}.json.gz','wt') as f:json.dump(dict(asset=a,hour=h,received_ts=time.time(),response=raw),f,separators=(',',':'))
  with path.open('a') as f:f.write(json.dumps(dict(asset=a,hour=h,seconds=len(seconds),minutes=minutes),separators=(',',':'))+'\n')
  if i%25==0:print('CF',i+1,'/',len(jobs),a,flush=True)
  time.sleep(max(0,1.1-(time.monotonic()-before)))
def control():
 # The 15m comparison keeps actual deployed Coinbase inputs and opening reference.
 rows=markets('KXBTC15M',START);candles('KXBTC15M',rows)
 path=DATA/'coinbase_BTC.jsonl';done={json.loads(l)['start'] for l in path.open()} if path.exists() else set()
 for i,t in enumerate(range(START-7200,END,290*60)):
  if t in done:continue
  raw=get('/products/BTC-USD/candles',dict(granularity=60,start=t,end=min(t+290*60,END)),base='https://api.exchange.coinbase.com')
  assert isinstance(raw['response'],list)
  with path.open('a') as f:f.write(json.dumps(dict(start=t,**raw),separators=(',',':'))+'\n')
  if i%30==0:print('Coinbase batches',i+1,flush=True)
if __name__=='__main__':
 mode=sys.argv[1]
 if mode=='markets':
  with ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(one,('KXBTCD','KXBTC','KXETHD')))
  save(DATA/'market_receipt.json',dict(start=START,end=END,results=result,finished=time.time()))
 elif mode=='benchmark':benchmark()
 elif mode=='control':control()
