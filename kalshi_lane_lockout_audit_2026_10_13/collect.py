"""Read-only all-lane zero-fill audit; no engine instances or account writes."""
import collections,gzip,hashlib,json,sqlite3,subprocess,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def gz(n,x):
 with gzip.open(P/n,'wt') as f:json.dump(x,f)
def protect():
 files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in LIVE.iterdir() if f.suffix=='.py' or f.name=='dashboard.css'}
 flags={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in (LIVE/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))}
 services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')}
 return dict(files=files,flags=flags,services=services)
before=protect();save('preservation_before.json',before)
(P/'source').mkdir(exist_ok=True)
for f in LIVE.glob('*.py'):(P/'source'/f.name).write_bytes(f.read_bytes())
c=sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True);c.execute('BEGIN')
orders=[json.loads(r[0]) for r in c.execute('select record from orders')]
attempts=[json.loads(r[0]) for r in c.execute('select record from order_attempts')]
zero=[r for r in attempts if r['status']=='unfilled'];tickers=sorted({r['ticker'] for r in zero})
query="select id,ts,event from events where json_extract(event,'$.ticker') in ("+','.join('?' for _ in tickers)+') order by id'
events=[dict(json.loads(raw),_id=i,_saved_ts=ts) for i,ts,raw in c.execute(query,tickers)]
span=c.execute('select min(ts),max(ts),count(*) from events').fetchone()
c.rollback();c.close()
gz('orders.json.gz',orders);gz('attempts.json.gz',attempts);gz('events.json.gz',events)
obs=[]
with (P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl').open() as f:
 for line in f:
  if not any(t in line for t in tickers):continue
  try:o=json.loads(line)
  except ValueError:continue
  if o.get('ticker') in tickers:obs.append(o)
gz('observer.json.gz',obs)
status=json.loads((LIVE/'state/status.json').read_text());save('status.json',status)
om={o['ticker']:o for o in orders};summary=[]
for r in zero:
 final=om[r['ticker']];T=r['signal']['decision_ts'];lane=r.get('lane','cushion_z2')
 later=[e for e in events if e.get('ticker')==r['ticker'] and e.get('decision_ts',0)>T]
 summary.append(dict(ticker=r['ticker'],asset=r['asset'],lane=lane,entry_et=datetime.fromtimestamp(r['created_ts'],ET).isoformat(),decision_ts=T,close_ts=r['close_ts'],zero_attempt=r.get('attempt_number',1),latest_attempt=final.get('attempt_number',1),latest_status=final['status'],latest_fill=final['filled'],requested=r.get('requested_count','1'),ioc_retry_version=r.get('ioc_retry_version'),later_events=len(later),later_statuses=dict(collections.Counter(e['status'] for e in later)),later_signal_records=sum('z' in e for e in later)))
save('zero_summary.json',summary)
after=protect();assert before==after
save('collection_receipt.json',dict(ts=time.time(),et=datetime.now(ET).isoformat(),orders=len(orders),attempts=len(attempts),zero_attempts=len(zero),zero_markets=len(tickers),retained_events=len(events),retained_books=len(obs),event_span=span,source_flags_services_unchanged=True,account_writes=0))
print(json.dumps(summary,indent=2))
