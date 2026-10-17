"""Bounded public GET research access, with raw response provenance."""
import json,time,gzip,hashlib,fcntl,requests,os,threading,re
from pathlib import Path
from datetime import datetime
P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
RAW=P/'raw';RAW.mkdir(exist_ok=True)
def ts(s):
 s=re.sub(r'\.(\d+)(?=[+-]\d\d:\d\d$)',lambda m:'.'+m[1][:6].ljust(6,'0'),s.replace('Z','+00:00'))
 return int(datetime.fromisoformat(s).timestamp())
START=ts('2026-08-01T04:00:00Z');END=ts('2026-10-10T04:00:00Z')
def save(path,x):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+str(os.getpid())+'.'+str(threading.get_ident())+'.tmp')
 if path.suffix=='.gz':
  with gzip.open(tmp,'wt') as f:json.dump(x,f,allow_nan=False)
 else:tmp.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
 tmp.replace(path)
def read(path):
 path=Path(path)
 if path.suffix=='.gz':
  with gzip.open(path,'rt') as f:return json.load(f)
 return json.loads(path.read_text())
def get(path,params=None,cache=True):
 assert path.startswith(('/markets','/events','/series','/milestones','/live_data','/historical','/margin/markets','/margin/funding_rates','/margin/exchange'))
 url=requests.Request('GET',BASE+path,params=params).prepare().url
 key=hashlib.sha256(url.encode()).hexdigest();dest=RAW/(key+'.json.gz')
 if cache and dest.exists():
  try:return read(dest)['response']
  except (EOFError,json.JSONDecodeError,OSError):dest.rename(dest.with_name(dest.name+'.incomplete.'+str(time.time_ns())))
 for attempt in range(5):
  with (P/'request_clock').open('a+') as f:
   fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);last=float(f.read() or 0)
   wait=max(0,last+.25-time.time());time.sleep(wait);f.seek(0);f.truncate();f.write(str(time.time()));f.flush()
  try:
   r=requests.get(url,timeout=25)
   if r.status_code in (400,404):raise ValueError(str(r.status_code)+' '+url)
   r.raise_for_status();x=r.json()
   save(dest,{'url':url,'retrieved_ts':time.time(),'response':x});return x
  except requests.RequestException:
   if attempt==4:raise
   time.sleep(2**attempt)
def paged(path,params,key):
 out=[];cursor='';seen=set()
 while True:
  x=get(path,{**params,**({'cursor':cursor} if cursor else {})});out+=x[key];cursor=x.get('cursor','')
  if not cursor:return out
  assert cursor not in seen;seen.add(cursor)
