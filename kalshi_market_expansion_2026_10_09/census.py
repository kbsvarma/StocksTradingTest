"""Public GET-only availability and liquidity screen; no credentials or orders."""
from pathlib import Path
import requests,json,time,threading,statistics
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
P=Path(__file__).resolve().parent;BASE='https://external-api.kalshi.com/trade-api/v2'
series=json.loads((P/'series.json').read_text())['series']
short=json.loads((P/'15min_series.json').read_text())
lock=threading.Lock();next_request=0.;now=int(time.time())
def get(path,params):
 global next_request
 for attempt in range(4):
  with lock:
   delay=max(0,next_request-time.monotonic());next_request=max(next_request,time.monotonic())+.45
  time.sleep(delay)
  try:
   r=requests.get(BASE+path,params=params,timeout=20);r.raise_for_status();return r.json()
  except requests.RequestException:
   if attempt==3:raise
   time.sleep(2**attempt)
def scan(s):
 t=s['ticker'];raw={}
 for key,params in [('open',dict(status='open',limit=100)),('settled24h',dict(status='settled',limit=1000,min_close_ts=now-86400,max_close_ts=now))]:
  pages=[];cursor=''
  while True:
   data=get('/markets',dict(series_ticker=t,**params,**({'cursor':cursor} if cursor else {})));pages.extend(data['markets']);cursor=data.get('cursor')
   if not cursor:break
  raw[key]=pages
 (P/'inventory_raw'/f'{t}.json').write_text(json.dumps(raw))
 recent=raw['settled24h'];vols=[float(x.get('volume_fp') or 0) for x in recent]
 return dict(ticker=t,title=s['title'],category=s.get('category'),frequency=s.get('frequency'),open_markets=len(raw['open']),settled24h=len(recent),positive_volume24h=sum(v>0 for v in vols),contracts24h=sum(vols),median_window_contracts=statistics.median(vols) if vols else 0,sources=s.get('settlement_sources'),fee_type=s.get('fee_type'),fee_multiplier=s.get('fee_multiplier'))
if __name__=='__main__':
 (P/'inventory_raw').mkdir(exist_ok=True)
 # Every short-duration definition, plus other continuous-price families.
 tickers={s['ticker'] for s in short}
 extra=[s for s in series if s['ticker'] not in tickers and s.get('category') in ('Financials','Crypto','Commodities') and (s.get('frequency')=='hourly' or s['ticker'] in ('KXINX','KXINXU','KXNASDAQ100','KXNASDAQ100U','KXSPY','KXQQQ','KXDJIA'))]
 selected=short+extra;out=[]
 with ThreadPoolExecutor(max_workers=2) as pool:
  futures={pool.submit(scan,s):s['ticker'] for s in selected}
  for f in as_completed(futures):
   try:
    r=f.result();out.append(r);print(r['ticker'],r['open_markets'],r['settled24h'],round(r['contracts24h']),flush=True)
   except Exception as e:out.append(dict(ticker=futures[f],error=str(e)));print('ERROR',futures[f],str(e),flush=True)
 (P/'inventory.json').write_text(json.dumps(dict(asof_utc=datetime.fromtimestamp(now,timezone.utc).isoformat(),snapshot_is_not_atomic=True,rows=out),indent=2)+'\n')
