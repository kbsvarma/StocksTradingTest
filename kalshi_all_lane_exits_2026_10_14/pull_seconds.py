"""GET-only historical CF source for every actual crypto trade, no trade transport."""
from pathlib import Path
import json,gzip,sys,time,requests,datetime,collections
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_revalidation_2026_09_12/live_v1'))
from kalshi_client import Credentials,KalshiClient
def main():
 rows=json.loads((P/'actual_entries.json').read_text());target=set();ids={'BTC':'BRTI','ETH':'ETHUSD_RTI','SOL':'SOLUSD_RTI','XRP':'XRPUSD_RTI'}
 for r in rows:
  if r['asset'] not in ids:continue
  for h in range((r['t']-1)//3600*3600,int(r['close']),3600):target.add((r['asset'],h))
 c=KalshiClient(creds=Credentials.load(),dry_run=True);s=requests.Session();path='/trade-api/v2/cfbenchmarks/history/values';out=P/'cf_seconds';out.mkdir(exist_ok=True)
 results=[]
 # Loss hours first only affects fetching order, not inclusion or model fitting.
 loss_hours={(r['asset'],r['t']//3600*3600) for r in rows if not r['won']}
 for n,(a,h) in enumerate(sorted(target,key=lambda x:(x not in loss_hours,x))):
  dest=out/f'{a}_{h}.json.gz'
  if dest.exists():continue
  while time.time()%60<8:time.sleep(.5)
  params=dict(id=ids[a],timespan='HOUR',timestamp=datetime.datetime.fromtimestamp(h,datetime.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z'))
  for attempt in range(2):
   try:
    started=time.time();r=s.get('https://external-api.kalshi.com'+path,params=params,headers=c._headers('GET',path),timeout=15)
    if r.status_code==429:time.sleep(30);continue
    r.raise_for_status();j=r.json();arr=j.get('data',{}).get('payload',[]);assert isinstance(arr,list) and len(arr)>0
    with gzip.open(dest,'wt',compresslevel=1) as f:json.dump(dict(started=started,received=time.time(),url=r.url,response=j),f,separators=(',',':'))
    results.append(dict(asset=a,hour=h,points=len(arr),status='ok'));break
   except Exception as e:
    results.append(dict(asset=a,hour=h,status='error',attempt=attempt,error=str(e)[:160]));time.sleep(3)
  (P/'seconds_fetch_status.json').write_text(json.dumps(dict(total=len(target),finished_files=len(list(out.glob('*.gz'))),receipts=results),indent=2))
  if n%10==0:print(n+1,'/',len(target),a,h,'files',len(list(out.glob('*.gz'))),flush=True)
  time.sleep(1.5)
 print('FINISHED',len(target),len(list(out.glob('*.gz'))),flush=True)
if __name__=='__main__':main()
