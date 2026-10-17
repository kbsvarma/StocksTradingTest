from pathlib import Path
import json,time,requests,datetime as dt
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(exist_ok=True)
END=int(dt.datetime(2026,10,10,tzinfo=dt.timezone.utc).timestamp())
def get(url,params=None):
 for n in range(4):
  r=requests.get(url,params=params,timeout=30)
  if r.status_code==200:return r.json()
  if r.status_code not in (429,500,502,503,504):raise ValueError((r.status_code,r.text[:100]))
  time.sleep(1+n)
 raise RuntimeError(url)
def pull(pair,gran,start):
 fn=D/(pair+'_'+str(gran)+'.json')
 if fn.exists():return
 out={};st=start
 while st<END:
  en=min(st+280*gran,END)
  x=get('https://api.exchange.coinbase.com/products/'+pair+'/candles',dict(granularity=gran,start=dt.datetime.fromtimestamp(st,dt.timezone.utc).isoformat(),end=dt.datetime.fromtimestamp(en,dt.timezone.utc).isoformat()))
  for r in x:
   if start<=r[0]<END:out[r[0]]=r
  st=en;time.sleep(.2)
 fn.write_text(json.dumps([out[t] for t in sorted(out)]));print(pair,gran,len(out),flush=True)
with ThreadPoolExecutor(max_workers=2) as ex:
 list(ex.map(lambda x:pull(*x),[('BTC-USD',86400,int(dt.datetime(2015,1,1,tzinfo=dt.timezone.utc).timestamp())),('ETH-USD',86400,int(dt.datetime(2016,6,1,tzinfo=dt.timezone.utc).timestamp())),('ETH-USD',3600,int(dt.datetime(2026,6,3,tzinfo=dt.timezone.utc).timestamp()))]))
