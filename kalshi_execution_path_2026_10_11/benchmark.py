"""GET-only paired latency probe. Does not import a live runner or submit orders."""
import sys,time,json,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'candidate'))
from kalshi_client import Credentials,KalshiClientV2
from account_reads import FreshAccountReads
from engine import Exchange
c=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);request=c._request
calls=[]
def get_only(method,path,**kw):
 assert method=='GET';calls.append(path);return request(method,path,**kw)
c._request=get_only
limits=c._request('GET','/account/limits')
reader=FreshAccountReads(c);ex=Exchange(c)
def seq(index):return ex.positions(),ex.orders(status='resting'),ex.balance(index)
rows=[]
try:
 # Two warm-up rounds then six paired measurements. Work outside entry slice.
 for i in range(8):
  sec=time.time()%60
  if sec<33:time.sleep(33-sec)
  elif sec>52:time.sleep(93-sec)
  item=dict(round=i,warmup=i<2)
  for name,fn in ([('sequential',seq),('parallel',reader.snapshot)] if i%2==0 else [('parallel',reader.snapshot),('sequential',seq)]):
   t=time.perf_counter();result=fn(2);item[name+'_ms']=1000*(time.perf_counter()-t)
   item[name+'_state']=[len(result[0]),len(result[1]),str(result[2])]
   time.sleep(.25)
  rows.append(item);time.sleep(.5)
finally:reader.close();c.session.close()
r=[r for r in rows if not r['warmup']];old=statistics.median(r['sequential_ms'] for r in r);new=statistics.median(r['parallel_ms'] for r in r)
out=dict(ts=time.time(),limits=limits,rows=rows,sequential_median_ms=old,parallel_median_ms=new,reduction_percent=100*(1-new/old),mode='GET_ONLY',caveat='Account-read latency only, not order matching or proof of extra fills.')
(P/'benchmark.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
