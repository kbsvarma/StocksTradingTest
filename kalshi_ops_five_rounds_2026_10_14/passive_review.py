"""Read-only server receipts; no exchange requests or production ledger writes."""
import json,sqlite3,collections,statistics,time,subprocess,hashlib
from pathlib import Path
from urllib.request import urlopen
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';now=time.time()
start=json.loads((P/'deployment_receipt.json').read_text())['ts'];final=json.loads((P/'phase4_deployment_receipt.json').read_text())['ts']
db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
events=[]
for ts,raw in db.execute('select ts,event from events where ts>=? order by id',(1792000865,)):
 r=json.loads(raw);r['_saved_ts']=ts;events.append(r)
orders=[json.loads(r) for r, in db.execute('select record from orders')];db.close()
s=json.loads((L/'state/status.json').read_text());q=json.loads((L/'state/dashboard_quote_status.json').read_text())
def stats(vals):
 vals=sorted(vals);return dict(n=len(vals),median=statistics.median(vals) if vals else None,p95=vals[min(len(vals)-1,int(len(vals)*.95))] if vals else None,max=max(vals) if vals else None)
def summarize(rows):
 first={};substantive={}
 for r in rows:
  key=(r.get('asset'),r.get('lane'),r.get('decision_ts'))
  first.setdefault(key,r)
  if r.get('status') not in ('data_error','decision_candle_unavailable','no_decision_quote','incomplete_spot'):substantive.setdefault(key,r)
 def measure(rs):return stats([r.get('scan_completed_ts',r['_saved_ts'])-r['decision_ts'] for r in rs])
 return dict(events=len(rows),decision_minutes=len({r.get('decision_ts') for r in rows}),first_all=measure(first.values()),
  active_window_all=measure(r for r in substantive.values() if r.get('status')!='outside_window'),
  by_asset={a:measure(r for k,r in first.items() if k[0]==a) for a in s['assets']},
  active_by_asset={a:measure(r for k,r in substantive.items() if k[0]==a and r.get('status')!='outside_window') for a in s['assets']},
  by_lane={lane:measure(r for k,r in first.items() if k[1]==lane) for lane in ('cushion_z2','premium_z3')},
  first_scan_started=stats([min(r.get('scan_started_ts',r['_saved_ts']) for r in rows if r.get('decision_ts')==t)-t for t in {r.get('decision_ts') for r in rows}]),
  status_counts=dict(collections.Counter(r.get('status') for r in rows)),errors=[r for r in rows if r.get('status')=='data_error'],
  account_ms=stats([r['execution_account_ms'] for r in rows if r.get('execution_account_ms') is not None]),
  book_gap_ms=stats([r['execution_book_gap_ms'] for r in rows if r.get('execution_book_gap_ms') is not None]),
  qualified=len({(r.get('ticker'),r.get('lane'),r['decision_ts']) for r in rows if r.get('signal_qualified')}),
  executable=len({(r.get('ticker'),r.get('lane'),r['decision_ts']) for r in rows if r.get('execution_executable')}))
before=[r for r in events if r['_saved_ts']<start and r.get('decision_ts',0)<int(start//60)*60]
after=[r for r in events if r['_saved_ts']>=final and r.get('decision_ts',0)>final and r['decision_ts']<int(now//60)*60]
new=[r for r in orders if r.get('created_ts',0)>final]
services={}
for name in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service'):
 services[name]=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
with urlopen('http://192.168.3.136:8507/_stcore/health',timeout=3) as response:health=response.read().decode()
initial=json.loads((P/'evidence/first_five_minute_review.json').read_text())
report=dict(initial_five_minute_check=initial,ts=now,deployment_ts=start,final_deployment_ts=final,before=summarize(before),after=summarize(after),
 status={k:s.get(k) for k in ('mode','armed','halted','error','stopped','contracts','max_contracts','max_risk','daily_loss_budget','execution_path_version','scan_start_seconds','fresh_retry_assets')},heartbeat_age=now-s['ts'],
 account=s.get('account'),new_orders=new,ui_health=health,quote_errors={a:r.get('error') for a,r in q['states'].items() if r.get('error')},services=services,ntp=subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip())
(P/'evidence/runtime_review.json').write_text(json.dumps(report,indent=2));(P/'evidence/comparison_events.json').write_text(json.dumps(events));(P/'evidence/after_status.json').write_text(json.dumps(s));print(json.dumps({k:report[k] for k in ('ts','before','after','heartbeat_age','status','new_orders')},indent=2))
