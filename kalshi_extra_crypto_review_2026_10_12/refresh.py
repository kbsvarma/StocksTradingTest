"""Public GET-only fresh market census, bounded bulk input refresh."""
import gzip,json,hashlib,shutil,subprocess,time,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests
import pull as p
P=p.P;OLD=P.parent/'kalshi_market_expansion_2026_10_09/crypto/data'
def save(n,x):p.write(P/n,x)
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
def digest(f):return hashlib.sha256(f.read_bytes()).hexdigest()
services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service')}
if not (P/'preservation_before.json').exists():
 save('preservation_before.json',dict(ts=time.time(),sources={x.name:digest(x) for x in L.glob('*.py')},flags={x.name:digest(x) for x in (L/'state').iterdir() if x.name.startswith(('ARM','HALT'))},services=services))
save('effective_live_status.json',json.loads((L/'state/status.json').read_text()))
def spot(a):
 dest=p.DATA/f'spot_{a}.jsonl';old=OLD/dest.name
 if not dest.exists() and old.exists():shutil.copyfile(old,dest)
 start=p.ts('2026-10-09T13:00:00Z') if old.exists() else p.START-7200
 done={json.loads(l)['start'] for l in dest.read_text().splitlines() if json.loads(l).get('review_extension')} if dest.exists() else set()
 session=requests.Session()
 for t in range(start,p.END,290*60):
  if t in done:continue
  for attempt in range(4):
   with p.lock:
    delay=max(0,p.next_request-time.monotonic());p.next_request=max(p.next_request,time.monotonic())+.8
   if delay:time.sleep(delay)
   try:
    reply=session.get('https://api.exchange.coinbase.com/products/'+a+'-USD/candles',params=dict(granularity=60,start=t,end=min(t+290*60,p.END)),timeout=15)
    reply.raise_for_status();raw=reply.json();assert isinstance(raw,list)
    with dest.open('a') as f:f.write(json.dumps(dict(start=t,end=min(t+290*60,p.END),review_extension=True,received_ts=time.time(),url=reply.url,rows=raw))+'\n')
    break
   except Exception:
    if attempt==3:raise
    time.sleep(2**attempt)
 session.close();print(a,'spot complete',flush=True)
def one(a):
 rows=p.markets(a)
 if rows:p.candles(a,rows);spot(a)
 return a,len(rows)
if __name__=='__main__':
 catalogue=p.get('/series')
 with gzip.open(P/'series_catalogue.json.gz','wt') as f:json.dump(catalogue,f)
 series=[s for s in catalogue['response']['series'] if s.get('frequency')=='fifteen_min' and ('Crypto' in s.get('categories',[]) or s.get('category')=='Crypto')]
 save('crypto_series.json',series)
 known={'KX'+a+'15M' for a in p.ASSETS}|{'KX'+a+'15M' for a in ('BTC','ETH','SOL','XRP')}|{'KXCRYPTOLEAD15M','KXCRYPTOCOMP15M'}
 save('unknown_crypto_series.json',[s for s in series if s['ticker'] not in known])
 assert not [s for s in series if s['ticker'] not in known],'New crypto series needs mapping'
 current={}
 for ticker in [s['ticker'] for s in series if s['ticker'] not in {'KX'+a+'15M' for a in ('BTC','ETH','SOL','XRP')}]:
  current[ticker]=p.get('/markets',dict(series_ticker=ticker,status='open',limit=100))
 save('open_markets.json',current)
 print('CENSUS',{k:len(v['response']['markets']) for k,v in current.items()},flush=True)
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,p.ASSETS))
 save('refresh_receipt.json',dict(finished=time.time(),counts=dict(results),start=p.START,end=p.END,public_get_only=True))
 print('DONE',results,flush=True)
