"""Read-only post-deployment verification and evidence capture."""
import json,sys,time,sqlite3,urllib.request
from pathlib import Path
from collections import Counter,defaultdict
from contextlib import closing
import deploy
P=Path(__file__).resolve().parent;R=json.loads((P/'deployment_receipt.json').read_text());started=json.loads((P/'deployment_started.json').read_text())['started_ts']
s=json.loads((deploy.STATE/'status.json').read_text());age=time.time()-s['ts']
assert age<20 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted'))
assert deploy.configs(s)==R['config'] and deploy.flags()==R['flags']
assert s['execution_path_version']==1 and s['account_reads']=='parallel_fresh_get'
assert s['account']['read_mode']=='parallel_fresh_get' and time.time()-s['account']['ts']<25
assert len(s['assets'])==7 and 'COPPER' not in s['assets']
assert all(x['asset']!='COPPER' for x in s['scans'])
deploy.validate_hashes(R['source_hashes'])
services={n:deploy.service(n) for n in deploy.SERVICES};assert services==R['services_after']
history=json.loads((Path(R['backup'])/'ledger_records.json').read_text());current=deploy.rows()
assert all(current.get(t)==raw for t,raw in history.items())
with closing(sqlite3.connect((deploy.STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
 assert db.execute('pragma quick_check').fetchone()[0]=='ok'
 events=[json.loads(r[0]) for r in db.execute('select event from events where ts>=?',(started,))]
 scans=[r for r in events if not r.get('kind')]
 assert scans and not any(r.get('asset')=='COPPER' for r in scans)
 assert not any(r.get('status')=='data_error' for r in scans)
 minutes=defaultdict(set)
 for r in scans:minutes[r.get('decision_ts')].add((r.get('lane'),r.get('asset')))
 complete=[m for m,v in minutes.items() if len(v)==14]
 assert len(complete)>=2,'Wait for two complete seven-market/two-lane scans'
health=urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=5)
assert health.status==200 and health.read().strip()==b'ok'
ui=json.loads((P/'ui_production.json').read_text());assert ui['passed'] and ui['archived_copper']==15
sys.path.insert(0,str(deploy.LIVE))
from operations_view import read_operations
from lanes import ACTIVE_ASSETS
funnel=read_operations(deploy.STATE/'orders.sqlite3',time.time()-86400,ACTIVE_ASSETS)
# Independent fresh exchange GETs; do not require flat now that live trading resumed.
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange,crypto,number
c=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);req=c._request

def get_only(method,*a,**kw):
 assert method=='GET';return req(method,*a,**kw)
c._request=get_only;e=Exchange(c)
try:
 positions={r['ticker']:str(number(r,'position_fp','position')) for r in e.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))}
 resting=[r.get('ticker') for r in e.orders(status='resting') if crypto(r.get('ticker',''))]
 balances={str(i):str(e.balance(i)) for i in (0,2)}
finally:c.session.close()
out=dict(passed=True,ts=time.time(),status_age_seconds=age,account=s['account'],services=services,completed_scan_minutes=complete,scan_counts=dict(Counter(r.get('status') for r in scans)),historical_rows_preserved=len(history),copper_archive_records=15,new_order_records=len(current)-len(history),stable_checkpoint_unchanged=True,config_unchanged=True,flags_unchanged=True,dashboard_health=200,ui_views_checked=15,funnel_24h=funnel['counts'],exchange_get_only=dict(positions=positions,resting=resting,balances=balances))
deploy.save(P/'verification.json',out);deploy.save(P/'post_status.json',s)
print(json.dumps(out,indent=2))
