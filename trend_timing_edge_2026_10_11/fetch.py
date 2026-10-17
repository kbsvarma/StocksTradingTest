from pathlib import Path
import requests,datetime as dt,time,json,gzip,concurrent.futures,shutil
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(exist_ok=True);UTC=dt.timezone.utc
a=int(dt.datetime(2022,1,1,tzinfo=UTC).timestamp());b=int(dt.datetime(2026,1,1,tzinfo=UTC).timestamp())
def fetch(job):
 asset,s=job;dest=D/(asset+'_'+str(s)+'.json')
 if dest.exists():return
 url='https://api.exchange.coinbase.com/products/'+asset+'-USD/candles'
 for n in range(5):
  z=requests.get(url,params={'granularity':3600,'start':dt.datetime.fromtimestamp(s,UTC).isoformat(),'end':dt.datetime.fromtimestamp(min(s+288*3600,b),UTC).isoformat()},timeout=30)
  if z.status_code==200:dest.write_text(z.text);time.sleep(1.05);return
  time.sleep(n+1)
 raise RuntimeError((asset,z.status_code))
jobs=[(asset,s) for asset in ['LTC','DOGE','ADA','BCH'] for s in range(a,b,288*3600)]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(fetch,jobs))
for asset in ['LTC','DOGE','ADA','BCH']:
 rows={}
 for f in D.glob(asset+'_*.json'):
  for c in json.load(open(f)):
   if a<=c[0]<b:rows[c[0]]=c
 with gzip.open(D/(asset+'.json.gz'),'wt') as f:json.dump({'rows':[rows[t] for t in sorted(rows)],'source':'Coinbase Exchange public candles','retrieved':dt.datetime.now(UTC).isoformat()},f)
 print(asset,len(rows),flush=True)
