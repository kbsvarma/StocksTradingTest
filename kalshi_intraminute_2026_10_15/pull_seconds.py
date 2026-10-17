"""Rate-limited GET-only history. Writes only this isolated experiment."""
from pathlib import Path
import datetime as dt,gzip,json,sys,time,requests,hashlib
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_revalidation_2026_09_12/live_v1'))
from kalshi_client import Credentials,KalshiClient
def main():
 start=int(dt.datetime(2026,9,4,tzinfo=dt.timezone.utc).timestamp())
 end=int(dt.datetime(2026,9,11,tzinfo=dt.timezone.utc).timestamp())
 ids={'BTC':'BRTI','ETH':'ETHUSD_RTI','SOL':'SOLUSD_RTI','XRP':'XRPUSD_RTI'}
 targets=[(a,h) for h in range(start-3600,end,3600) for a in ids]
 out=P/'cf_seconds';out.mkdir(exist_ok=True)
 c=KalshiClient(creds=Credentials.load(),dry_run=True);s=requests.Session()
 path='/trade-api/v2/cfbenchmarks/history/values';log=[]
 for n,(a,h) in enumerate(targets):
  dest=out/f'{a}_{h}.json.gz'
  if dest.exists():
   try:
    assert json.load(gzip.open(dest,'rt'))['response']['data']['payload']
    continue
   except Exception:dest.rename(dest.with_suffix('.incomplete'))
  while time.time()%60<8:time.sleep(.5)
  params={'id':ids[a],'timespan':'HOUR','timestamp':dt.datetime.fromtimestamp(h,dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')}
  for attempt in range(2):
   try:
    started=time.time();r=s.get('https://external-api.kalshi.com'+path,params=params,headers=c._headers('GET',path),timeout=15)
    if r.status_code==429:time.sleep(30);continue
    r.raise_for_status();j=r.json();arr=j['data']['payload'];assert isinstance(arr,list) and arr
    with gzip.open(dest,'wt',compresslevel=1) as f:json.dump(dict(started=started,received=time.time(),url=r.url,response=j),f,separators=(',',':'))
    log.append(dict(asset=a,hour=h,points=len(arr),sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),status='ok'));break
   except Exception as e:log.append(dict(asset=a,hour=h,error=str(e)[:180],status='error'));time.sleep(3)
  (P/'fetch_status.json').write_text(json.dumps(dict(total=len(targets),files=len(list(out.glob('*.gz'))),receipts=log),indent=2))
  if n%20==0:print(n+1,len(targets),a,h,flush=True)
  time.sleep(.25)
 print('DONE',len(list(out.glob('*.gz'))),flush=True)
if __name__=='__main__':main()
