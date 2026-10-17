"""Read-only server receipt capture. Writes only this research directory."""
import datetime as dt,gzip,hashlib,json,sqlite3,time,subprocess,shutil
from pathlib import Path
P=Path(__file__).resolve().parent;L=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
START=dt.datetime.fromisoformat('2026-10-08T00:00:00-04:00').timestamp()
ASSETS={'BTC','ETH','SOL','XRP','NATGAS','WTI','EURUSD','USDCAD'}
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def gz(n,x):
 with gzip.open(P/n,'wt') as f:json.dump(x,f,separators=(',',':'))
def preserve():
 names=('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service')
 return dict(ts=time.time(),hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in L.iterdir() if f.suffix in ('.py','.json') or f.name=='dashboard.css'},flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))},services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in names})
save('preservation_before.json',preserve());capture=time.time()
c=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=3);c.execute('begin')
orders=[json.loads(x[0]) for x in c.execute('select record from orders')]
orders=[x for x in orders if x.get('created_ts',0)>=START and x.get('asset') in ASSETS and float(x.get('filled',0))>0]
tickers={x['ticker'] for x in orders};attempts=[json.loads(x[0]) for x in c.execute('select record from order_attempts')];attempts=[x for x in attempts if x.get('ticker') in tickers]
events=[]
for ident,t,raw in c.execute('select id,ts,event from events where ts>=? and ts<=? order by id',(START-900,capture)):
 x=json.loads(raw)
 if x.get('ticker') in tickers:x['_id']=ident;x['_saved_ts']=t;events.append(x)
c.close();gz('orders.json.gz',orders);gz('attempts.json.gz',attempts);gz('events.json.gz',events)
observer=[];bad=0
with Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl').open() as f:
 for line in f:
  try:x=json.loads(line)
  except ValueError:bad+=1;continue
  if x.get('ticker') in tickers and START-900<=x.get('observed_ts',x.get('ts',0))<=capture:observer.append(x)
gz('observer.json.gz',observer)
(P/'production_source').mkdir(exist_ok=True)
for n in ('lanes.py','price_rules.py','engine.py','live_runner.py','execution_retry.py','sizing.py'):
 if (L/n).exists():shutil.copy2(L/n,P/'production_source'/n)
save('capture_receipt.json',dict(captured_ts=capture,start=START,orders=len(orders),attempts=len(attempts),events=len(events),observer=len(observer),observer_incomplete_lines=bad,production_db_read_only=True))
save('preservation_after_capture.json',preserve());print(json.dumps(dict(orders=len(orders),attempts=len(attempts),events=len(events),observer=len(observer))))
