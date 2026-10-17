"""Read public activity from previously frozen wallets; no account access."""
import requests,time,json,gzip,hashlib,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import P,read,save,ts
OLD=P.parent/'research_data/StocksTradingTest/kalshi_teacher_signal_2026_08/data/result_month.json'
START=ts('2026-08-12T04:00:00Z');END=ts('2026-10-10T04:00:00Z');D=P/'teachers';D.mkdir(exist_ok=True)
lock=threading.Lock();last=0

def get(wallet,a,b,offset=0):
 global last
 q={'user':wallet,'type':'TRADE','side':'BUY','start':a,'end':b,'sortBy':'TIMESTAMP','sortDirection':'DESC','limit':500,'offset':offset};url=requests.Request('GET','https://data-api.polymarket.com/activity',params=q).prepare().url;f=D/(hashlib.sha256(url.encode()).hexdigest()+'.json.gz')
 if f.exists():return read(f)['rows']
 for retry in range(5):
  with lock:
   time.sleep(max(0,last+.20-time.monotonic()));last=time.monotonic()
  r=requests.get(url,timeout=25)
  if r.status_code in (401,403):r.raise_for_status()
  if r.status_code==429 or r.status_code>=500:
   time.sleep(2**retry);continue
  r.raise_for_status();x=r.json();assert isinstance(x,list);save(f,{'url':url,'retrieved':time.time(),'rows':x});return x
 raise RuntimeError('public activity request failed')
def interval(wallet,a,b):
 x=get(wallet,a,b)
 if len(x)<500:return x
 if b>a:
  mid=(a+b)//2;return interval(wallet,a,mid)+interval(wallet,mid+1,b)
 rows=x
 for offset in range(500,5001,500):
  x=get(wallet,a,b,offset);rows+=x
  if len(x)<500:return rows
 raise RuntimeError('same-second activity over pagination limit')
def pull(leader):
 wallet=leader['proxyWallet'];out=[];errors=[]
 for a in range(START,END,7*86400):
  try:out+=interval(wallet,a,min(a+7*86400-1,END-1))
  except Exception as e:errors.append({'start':a,'error':str(e)})
 out=sorted(out,key=lambda r:r['timestamp']);save(D/(wallet+'.json.gz'),out)
 receipt={'wallet':wallet,'name':leader.get('userName'),'rows':len(out),'errors':errors,'min_ts':out[0]['timestamp'] if out else None,'max_ts':out[-1]['timestamp'] if out else None};print(receipt,flush=True);return receipt
if __name__=='__main__':
 old=read(OLD);qualified=set(old['qualified_teachers']);leaders=[x for x in old['leaderboard'] if x['proxyWallet'] in qualified];assert len(leaders)==len(qualified)==8
 save(D/'frozen_teachers.json',{'old_generated':old['generated_utc'],'leaders':leaders,'source':str(OLD)})
 with ThreadPoolExecutor(max_workers=3) as ex:receipts=list(ex.map(pull,leaders))
 save(D/'receipt.json',receipts)
