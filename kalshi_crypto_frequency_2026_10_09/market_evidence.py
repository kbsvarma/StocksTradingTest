"""Small paced public GET audit plus existing observer data. No order access."""
import json,gzip,time,requests,shutil,sqlite3
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York');BASE='https://api.elections.kalshi.com/trade-api/v2';s=requests.Session()
now=json.loads((P/'receipt.json').read_text())['ts'];start=int(datetime(2026,10,3,tzinfo=ET).timestamp());calls=[]
def get(path,params=None):
 # Avoid the trading process's principal request burst at the start of a minute.
 phase=time.time()%60
 if phase<30:time.sleep(30-phase)
 r=s.get(BASE+path,params=params,timeout=8);calls.append(dict(url=r.url,status=r.status_code,received=time.time()))
 r.raise_for_status();time.sleep(.3);return r.json()
markets={}
if (P/'markets.json.gz').exists():
 with gzip.open(P/'markets.json.gz','rt') as f:markets=json.load(f)
for a in ('BTC','ETH','SOL','XRP'):
 if a in markets:continue
 result=[];cursor=''
 while True:
  params=dict(series_ticker='KX'+a+'15M',min_close_ts=start-1,max_close_ts=int(now),limit=1000)
  if cursor:params['cursor']=cursor
  d=get('/markets',params);result+=d['markets'];cursor=d.get('cursor')
  if not cursor:break
 markets[a]=result
with gzip.open(P/'markets.json.gz','wt') as f:json.dump(markets,f)
try:ann=get('/exchange/announcements')
except requests.HTTPError as exc:ann={'unavailable':str(exc),'status':exc.response.status_code}
(P/'exchange_announcements.json').write_text(json.dumps(ann,indent=2))
(P/'public_requests.json').write_text(json.dumps(calls,indent=2))
observer=P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl'
observed=[]
with observer.open() as f:
 for line in f:
  try:r=json.loads(line)
  except ValueError:continue
  if r.get('decision_ts',r.get('observed_ts',r.get('ts',0)))>=start:observed.append(r)
with gzip.open(P/'observer.json.gz','wt') as f:json.dump(observed,f)
summary={}
for a,rr in markets.items():
 out=defaultdict(list)
 for r in rr:out[datetime.fromisoformat(r['open_time'].replace('Z','+00:00')).astimezone(ET).date().isoformat()].append(r)
 summary[a]={d:dict(markets=len(v),contracts_volume=sum(float(x.get('volume_fp',x.get('volume',0))) for x in v),zero_volume=sum(float(x.get('volume_fp',x.get('volume',0)))==0 for x in v)) for d,v in sorted(out.items())}
(P/'volume.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(dict(volume=summary,observer_records=len(observed),public_requests=len(calls)),indent=2))
