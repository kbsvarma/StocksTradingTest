"""Read-only production snapshot and event funnel. Writes only this audit directory."""
import sqlite3,json,gzip,time,hashlib,subprocess,math
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal as D
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';ET=ZoneInfo('America/New_York')
START=int(datetime(2026,10,9,11,7,tzinfo=ET).timestamp());RECENT=int(datetime(2026,10,9,tzinfo=ET).timestamp())
ASSETS=('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI');LANES=('cushion_z2','premium_z3')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def stats(v):
 v=sorted(v);n=len(v)
 return dict(n=n,median=v[n//2] if n else None,p95=v[min(n-1,int(.95*n))] if n else None,max=v[-1] if n else None)
def gz(n,rs):
 with gzip.open(P/n,'wt') as f:json.dump(rs,f)
src=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True);db=sqlite3.connect(P/'audit.sqlite3');src.backup(db);src.close();now=time.time();END=int(now//60)*60
status=json.loads((LIVE/'state/status.json').read_text());save('status.json',status)
(P/'source').mkdir(exist_ok=True)
for f in LIVE.glob('*.py'):(P/'source'/f.name).write_bytes(f.read_bytes())
hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in LIVE.glob('*.py')};save('source_hashes.json',hashes)
arms={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (LIVE/'state').iterdir() if f.is_file() and (f.name.startswith('ARM') or f.name.startswith('HALT'))};save('arm_halt_hashes.json',arms)
orders=[json.loads(r[0]) for r in db.execute('SELECT record FROM orders')];attempts=[json.loads(r[0]) for r in db.execute('SELECT record FROM order_attempts')];om={r['ticker']:r for r in orders};gz('orders.json.gz',orders);gz('attempts.json.gz',attempts)
hist=defaultdict(list);by_ticker=defaultdict(list);events=[]
for ident,saved,raw in db.execute('SELECT id,ts,event FROM events WHERE ts>=? ORDER BY id',(RECENT,)):
 e=json.loads(raw);e['_id']=ident;e['_saved_ts']=saved;e['lane']=e.get('lane',e.get('rule','cushion_z2'));T=e.get('decision_ts')
 if e.get('asset') not in ASSETS or e['lane'] not in LANES or not T:continue
 events.append(e);hist[e['asset'],e['lane'],T].append(e);by_ticker[e.get('ticker')].append(e)
gz('events.json.gz',events)
keys=[k for k in hist if START<=k[2]<END];expected={(a,l,T) for T in range(START,END,60) for a in ASSETS for l in LANES};missing=expected-set(keys)
save('coverage.json',dict(start_et=datetime.fromtimestamp(START,ET).isoformat(),end_exclusive_et=datetime.fromtimestamp(END,ET).isoformat(),expected=len(expected),recorded=len(expected)-len(missing),missing=len(missing),missing_keys=[list(k) for k in sorted(missing)],first_latency=stats([hist[k][0]['_saved_ts']-k[2] for k in keys]),first_status=dict(Counter(hist[k][0]['status'] for k in keys)),final_status=dict(Counter(hist[k][-1]['status'] for k in keys))))
def qualified(e):
 a=e.get('asset');l=e['lane'];rule=status['assets'][a]['rules'][l];m=e.get('remaining_minutes',0)
 return isinstance(e.get('z'),(int,float)) and e['z']>=e.get('required_z',rule['z_base_at_5m']+rule['z_slope']*math.log2(m/5)) and rule['min_minutes']<=m<=rule['max_minutes']
qevents=[e for e in events if START<=e['decision_ts']<END and qualified(e)];qmap=defaultdict(list)
for e in qevents:qmap[e['ticker']].append(e)
miss={t:es for t,es in qmap.items() if t not in om or D(str(om[t].get('filled',0)))==0};save('qualified_unfilled.json',miss)
def funnel(asset=None,lane=None,start=START,end=END):
 es=[e for e in events if start<=e['decision_ts']<end and (asset is None or e['asset']==asset) and (lane is None or e['lane']==lane)]
 qs={e['ticker'] for e in es if qualified(e)};bookq={e['ticker'] for e in es if e['status'] in ('eligible','paper_fill')}
 rr=[r for r in orders if start<=r['created_ts']<end and (asset is None or r.get('asset')==asset) and (lane is None or r.get('lane','cushion_z2')==lane)];fs=[r for r in rr if D(str(r.get('filled',0)))>0];settled=[r for r in fs if r['status']=='settled']
 filled_any={t for t in qs if t in om and D(str(om[t].get('filled',0)))>0};other={t for t in filled_any if lane is not None and om[t].get('lane','cushion_z2')!=lane}
 ks=[k for k in hist if start<=k[2]<end and (asset is None or k[0]==asset) and (lane is None or k[1]==lane)]
 return dict(qualified_markets=len(qs),qualified_filled_any_lane=len(filled_any),qualified_filled_other_lane=len(other),qualified_unfilled=sorted(qs-filled_any),book_eligible_markets=len(bookq),attempted_markets=len(rr),order_submissions=sum(r.get('attempt_number',1) for r in rr),filled=len(fs),settled=len(settled),wins=sum(r['side']==r.get('result') for r in settled),losses=sum(r['side']!=r.get('result') for r in settled),net=str(sum((D(r['pnl']) for r in settled),D(0))),fees=str(sum((D(r.get('fees','0')) for r in settled),D(0))),partial_fills=sum(D(r['filled'])<D(str(r.get('requested_count',1))) for r in fs),first_status=dict(Counter(hist[k][0]['status'] for k in ks)),final_status=dict(Counter(hist[k][-1]['status'] for k in ks)),execution_status=dict(Counter(e.get('execution_status') for e in es if e.get('execution_status'))),first_latency=stats([hist[k][0]['_saved_ts']-k[2] for k in ks]),filled_retry_checks=dict(Counter(r.get('signal',{}).get('execution_check_number',1) for r in fs)))
summary=dict(ts=now,et=datetime.fromtimestamp(now,ET).isoformat(),window_start_et=datetime.fromtimestamp(START,ET).isoformat(),all=funnel(),by_asset={a:funnel(asset=a) for a in ASSETS},by_lane={l:funnel(lane=l) for l in LANES},by_asset_lane={a+'/'+l:funnel(a,l) for a in ASSETS for l in LANES})
summary['daily']={}
for date in ('2026-10-09','2026-10-10','2026-10-11'):
 lo=int(datetime.fromisoformat(date).replace(tzinfo=ET).timestamp());hi=min(END,lo+86400);summary['daily'][date]=dict(all=funnel(start=lo,end=hi),by_asset={a:funnel(asset=a,start=lo,end=hi) for a in ASSETS})
save('summary.json',summary)
issues=[hist[k] for k in keys if any(e['status'] not in ('price_gate','cushion_gate','outside_time_window','outside_window','no_current_market','already_entered','book_price','book_price_gate','book_depth','paper_fill','eligible') for e in hist[k])];save('nonstandard_traces.json',issues)
assert all(D(r['pnl'])==(D(r['filled']) if r['side']==r['result'] else D(0))-D(r['cost'])-D(r['fees']) for r in orders if r['status']=='settled')
services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service')}
save('receipt.json',dict(ts=now,services=services,db_integrity=db.execute('PRAGMA quick_check').fetchone()[0],orders_total=len(orders),attempts_total=len(attempts),ledger_arithmetic=True,live_writes=False))
observer=Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state');save('observer_status.json',json.loads((observer/'status.json').read_text()));obs=[]
with (observer/'observations.jsonl').open() as f:
 for line in f:
  try:o=json.loads(line)
  except ValueError:continue
  if o.get('decision_ts',o.get('ts',0))>=START:obs.append(o)
gz('observer.json.gz',obs)
print('WINDOW',summary['window_start_et'],summary['et']);print('COVERAGE',len(expected),len(missing));print('FIRST/FINAL',Counter(hist[k][0]['status'] for k in keys),Counter(hist[k][-1]['status'] for k in keys))
for a,v in summary['by_asset'].items():print(a,{k:v[k] for k in ('qualified_markets','qualified_filled_any_lane','qualified_unfilled','order_submissions','filled','wins','losses','net','partial_fills')})
print('LANES',{l:{k:v[k] for k in ('qualified_markets','qualified_filled_any_lane','qualified_filled_other_lane','order_submissions','filled','wins','losses','net')} for l,v in summary['by_lane'].items()});print('MISSED',len(miss));print('OBSERVER',len(obs))
