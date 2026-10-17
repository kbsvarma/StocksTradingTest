import json,time,urllib.request,urllib.parse,datetime
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(exist_ok=True)
BASE='https://api.elections.kalshi.com/trade-api/v2'
def get(path,params=None):
 url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
 for i in range(7):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Kalshi-independent-research/1.0'}),timeout=45) as f:r=json.load(f)
   time.sleep(.3);return r
  except Exception:
   if i==6:raise
   time.sleep(min(3*(i+1),20))
def main():
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();r=get('/series',{'include_volume':'true'});(D/'series.json').write_text(json.dumps(r))
 cursor='';seen=set();events={};pages=0
 with (D/'events.jsonl').open('w') as out:
  while True:
   q={'limit':200,'status':'open','with_nested_markets':'true'}
   if cursor:q['cursor']=cursor
   r=get('/events',q);pages+=1
   for e in r.get('events',[]):events[e['event_ticker']]=e;out.write(json.dumps(e)+'\n')
   out.flush();cursor=r.get('cursor','')
   if pages%5==0:print('pages',pages,'events',len(events),'markets',sum(len(e.get('markets',[])) for e in events.values()),flush=True)
   if not cursor:break
   if cursor in seen:raise RuntimeError('Repeated cursor')
   seen.add(cursor)
 (D/'census_receipt.json').write_text(json.dumps({'started_utc':started,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pages':pages,'events':len(events),'cursor_exhausted':True,'series':len(json.loads((D/'series.json').read_text())['series'])},indent=2))
 print('COMPLETE',len(events),flush=True)
if __name__=='__main__':main()
