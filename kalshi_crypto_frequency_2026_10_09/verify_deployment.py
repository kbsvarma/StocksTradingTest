"""Read-only verification of the deployed runner, current scans and preservation."""
import fcntl,json,sqlite3,subprocess,time
from pathlib import Path
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import deploy_resilience as d
P=d.P;LIVE=d.LIVE;STATE=d.STATE
r=json.loads((P/'deployment_receipt.json').read_text())
started=json.loads((P/'deployment_started.json').read_text())['started_ts']
s=json.loads((STATE/'status.json').read_text());now=time.time()
assert now-s['ts']<10 and s['ts']>=started
assert s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted'))
assert d.configs(s)==r['config']
assert d.flags()==r['flags']
assert d.validate_hashes(r['source_hashes'])==12
services={n:d.service(n) for n in d.SERVICES}
assert all(v['ActiveState']=='active' for v in services.values())
assert services==r['services_after'],'Unexpected service restart since deployment'
with (STATE/'process.lock').open('r') as lock:
 try:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:lock_held=True
 else:
  fcntl.flock(lock,fcntl.LOCK_UN);lock_held=False
assert lock_held,'Live singleton lock not held'
history=json.loads((Path(r['backup'])/'ledger_records.json').read_text());current=d.rows()
assert all(current.get(t)==raw for t,raw in history.items()),'Historical rows changed'
scans=s['scans'];complete={(x.get('lane'),x['asset']) for x in scans if x['decision_ts']>=started}
assert len(complete)==16,'Wait for a full post-deployment scan minute'
for x in scans:
 if x['asset']=='COPPER':assert x['status']=='activation_blocked' and x['execution_status']=='COPPER manually halted'
 assert x.get('status')!='data_error',x
minutes={};errors=[]
with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
 for ts,raw in db.execute('select ts,event from events where ts>=? order by id',(started,)):
  e=json.loads(raw);key=(e.get('lane'),e.get('asset'),e.get('decision_ts'))
  minutes.setdefault(key,e)
  if e.get('status') in ('data_error','error') or e.get('execution_error'):errors.append(e)
ui=requests.get('http://192.168.3.136:8507/_stcore/health',timeout=5)
assert ui.status_code==200 and ui.text.strip()=='ok'
warnings=subprocess.check_output(['journalctl','--user','-u',d.SERVICE,'--since','@'+str(int(started)),'--no-pager','-p','warning','-o','cat'],text=True)
new=[json.loads(raw) for t,raw in current.items() if t not in history]
result=dict(verified_et=datetime.now(ZoneInfo('America/New_York')).isoformat(),started_ts=started,heartbeat_age=now-s['ts'],mode=s['mode'],services=services,source_hashes=r['source_hashes'],stable_checkpoint_files=12,
 historical_rows_preserved=len(history),post_deployment_decision_records=len(minutes),post_deployment_data_errors=errors,
 latest_scans=[{k:x.get(k) for k in ('lane','asset','ticker','decision_ts','status','execution_status','z','signal_ask')} for x in scans],
 copper_paused=True,config=d.configs(s),account=s['account'],active=s['active'],new_trades=new,ui_health=ui.status_code,singleton_lock_held=lock_held,journal_warnings=warnings.strip())
d.save(P/'deployment_verification.json',result)
print(json.dumps({k:v for k,v in result.items() if k not in ('source_hashes','latest_scans','config','new_trades','active')},indent=2))
