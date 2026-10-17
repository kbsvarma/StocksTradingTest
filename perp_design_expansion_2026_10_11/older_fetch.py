from pathlib import Path
import requests,datetime as dt,time,json,gzip,concurrent.futures
P=Path(__file__).resolve().parent;D=P/'older_zec';D.mkdir(exist_ok=True);UTC=dt.timezone.utc
start=int(dt.datetime(2022,1,1,tzinfo=UTC).timestamp());end=int(dt.datetime(2026,1,1,tzinfo=UTC).timestamp());url='https://api.exchange.coinbase.com/products/ZEC-USD/candles'
def fetch(s):
 f=D/(str(s)+'.json')
 if f.exists():return json.load(open(f))
 params={'granularity':3600,'start':dt.datetime.fromtimestamp(s,UTC).isoformat(),'end':dt.datetime.fromtimestamp(min(s+288*3600,end),UTC).isoformat()}
 for n in range(5):
  z=requests.get(url,params=params,timeout=25)
  if z.status_code==200:
   x=z.json();f.write_text(json.dumps(x));time.sleep(1.05);return x
  time.sleep(n+1)
 raise RuntimeError(z.status_code)
rows={}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 for x in ex.map(fetch,range(start,end,288*3600)):
  for c in x:
   if start<=c[0]<end:rows[int(c[0])]=c
with gzip.open(P/'older_zec_hourly.json.gz','wt') as f:json.dump({'source':url,'retrieved':dt.datetime.now(UTC).isoformat(),'rows':[rows[t] for t in sorted(rows)]},f)
print('hours',len(rows),flush=True)
