"""Authorized maintenance: seven source files only; no account writes or config changes."""
import argparse,fcntl,hashlib,json,os,shutil,sqlite3,subprocess,sys,time
from contextlib import closing
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';STATE=LIVE/'state'
TRADER='kalshi-crypto-z2-live.service';UI='kalshi-dashboard.service'
SERVICES=(TRADER,UI,'kalshi-execution-observer.service','kalshi-tennis90-paper.service')
CHANGED=('account_reads.py','engine.py','runner.py','lanes.py','market_data.py','operations_view.py','dashboard_panel.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def service(n):return dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
def flags():return {f.name:sha(f) for f in STATE.iterdir() if f.is_file() and f.name.startswith(('HALT','ARM'))}
def rows():
 with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:return {r[0]:r[1] for r in db.execute('select ticker,record from orders')}
def configs(s):
 return dict(settings={k:s[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget','signal_ask_ceiling_dollars','execution_cap_dollars','z_min','minutes','execution_checks_max','execution_retry_deadline_seconds','max_ioc_submissions','zero_fill_retry_enabled','pending_reconcile_seconds')},
  assets={a:{k:v[k] for k in ('enabled','blocked','contracts','exchange_index','rules')} for a,v in s['assets'].items() if a!='COPPER'},
  lanes={n:{k:v[k] for k in ('contracts','z_min','signal_floor','signal_ceiling','execution_cap','enabled','blocked','max_contracts','max_risk','daily_loss_budget')} for n,v in s['lanes'].items()})
def validate_hashes(expected):
 actual={p.name:sha(p) for p in LIVE.iterdir() if p.suffix=='.py' or p.name=='dashboard.css'}
 assert actual==expected,'Live source drift: '+str([k for k in set(actual)|set(expected) if actual.get(k)!=expected.get(k)])
 checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
 manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256']
 assert all(sha(checkpoint/'source'/n)==h for n,h in manifest.items()),'Stable checkpoint changed'
 return len(manifest)
def readonly_account(require_live=True):
 from kalshi_client import Credentials,KalshiClientV2
 from engine import Exchange,crypto,number,ACTIVE
 import runner
 s=json.loads((STATE/'status.json').read_text())
 if require_live:assert time.time()-s['ts']<30 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')),'Live status blocked/stale'
 assert not runner.live_permission(),'Permission guard blocked'
 assert (STATE/'HALT_COPPER').exists(),'Preserve Copper halt'
 assert not (STATE/'HALT').exists() and not (STATE/'HALT_Z3').exists()
 ledger=rows();assert not any(json.loads(r)['status'] in ACTIVE for r in ledger.values()),'Wait for pending/open trades to reconcile'
 client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);request=client._request
 def get_only(method,*a,**kw):
  assert method=='GET','Deployment cannot submit/cancel orders'
  return request(method,*a,**kw)
 client._request=get_only;ex=Exchange(client)
 try:
  positions=[r for r in ex.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))]
  resting=[r for r in ex.orders(status='resting') if crypto(r.get('ticker',''))]
  assert not positions and not resting,'Exchange exposure requires reconciliation'
  balances={str(i):str(ex.balance(i)) for i in (0,2)}
 finally:client.session.close()
 return dict(ts=time.time(),mode='GET_ONLY',supported_positions=0,supported_resting_orders=0,pending_or_open_records=0,ledger_records=len(ledger),balances=balances)
def replace_file(src,dest):
 tmp=dest.with_name(dest.name+'.execution-upgrade');shutil.copy2(src,tmp)
 with tmp.open('rb') as f:os.fsync(f.fileno())
 os.replace(tmp,dest)
 fd=os.open(str(dest.parent),os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 assert not (P/'deployment_receipt.json').exists(),'Already deployed; inspect receipt'
 expected=json.loads((P/'original_hashes.json').read_text());bundle=json.loads((P/'candidate_manifest.json').read_text())
 assert all(sha(P/'candidate'/n)==h for n,h in bundle.items()),'Candidate drift after tests'
 assert set(CHANGED)=={n for n,h in bundle.items() if not n.startswith('test_') and n!='preview.py' and expected.get(n)!=h},'Unapproved source changes'
 assert 'Ran 191 tests' in (P/'server_tests.log').read_text() and (P/'server_tests.log').read_text().rstrip().endswith('OK')
 preview=json.loads((P/'ui_preview.json').read_text());assert preview['passed'] and len(preview['views'])==15
 benchmark=json.loads((P/'benchmark.json').read_text());assert benchmark['mode']=='GET_ONLY' and benchmark['parallel_median_ms']<benchmark['sequential_median_ms']
 checkpoint_count=validate_hashes(expected)
 sys.path.insert(0,str(LIVE))
 before=json.loads((STATE/'status.json').read_text());config_before=configs(before);flags_before=flags()
 assert not before['assets']['COPPER']['enabled'] and len(config_before['assets'])==7
 services_before={n:service(n) for n in SERVICES};assert all(v['ActiveState']=='active' for v in services_before.values())
 preflight=readonly_account();save(P/'deployment_preflight.json',dict(account=preflight,config=config_before,flags=flags_before,services=services_before,tests=191))
 print('Preflight PASS: flat account; all limits unchanged; seven file maintenance deployment.',flush=True)
 if not args.execute:return
 second=time.time()%60
 if second<33:time.sleep(33-second)
 elif second>45:time.sleep(93-second)
 backup=P/('deployment_backup_'+str(time.time_ns()));backup.mkdir()
 save(backup/'flags.json',flags_before);save(backup/'services.json',services_before)
 modified=[];lock=None;stopped=[]
 try:
  for unit in (TRADER,UI):
   stopped.append(unit);subprocess.run(['systemctl','--user','stop',unit],check=True,timeout=40)
  lock=(STATE/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  validate_hashes(expected);assert flags()==flags_before
  save(backup/'stopped_account.json',readonly_account(require_live=False))
  ledger_before=rows();save(backup/'ledger_records.json',ledger_before)
  with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
   with closing(sqlite3.connect(backup/'orders.sqlite3')) as target:source.backup(target)
  for name in CHANGED:
   if (LIVE/name).exists():shutil.copy2(LIVE/name,backup/name)
  for name in CHANGED:
   modified.append(name);replace_file(P/'candidate'/name,LIVE/name)
  deployed=dict(expected,**{n:bundle[n] for n in CHANGED});validate_hashes(deployed)
  subprocess.run([sys.executable,'-c','import runner,engine,account_reads,operations_view,lanes,market_data; assert runner.CONTRACTS==5; assert runner.MAX_CONTRACTS==25; assert len(lanes.ACTIVE_ASSETS)==7; assert "COPPER" not in market_data.ASSETS; assert "COPPER" in lanes.ALL_ASSETS'],cwd=LIVE,check=True)
  assert rows()==ledger_before and flags()==flags_before,'State changed while stopped'
 except BaseException:
  for name in reversed(modified):
   if (backup/name).exists():replace_file(backup/name,LIVE/name)
   elif (LIVE/name).exists():os.replace(LIVE/name,backup/('rolled_back_'+name))
  validate_hashes(expected)
  if lock is not None:lock.close()
  for unit in stopped:subprocess.run(['systemctl','--user','start',unit],check=True)
  raise
 finally:
  if lock is not None and not lock.closed:lock.close()
 started=time.time()
 for unit in (TRADER,UI):subprocess.run(['systemctl','--user','start',unit],check=True)
 save(P/'deployment_started.json',dict(started_ts=started,backup=str(backup),source_hashes=deployed,flags=flags_before))
 # Once trading resumes, never restore a ledger or blindly roll back over orders.
 for _ in range(50):
  time.sleep(1);s=json.loads((STATE/'status.json').read_text())
  if s['ts']>=started and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')):
   assert configs(s)==config_before,'Effective configuration changed'
   assert len(s['assets'])==7 and 'COPPER' not in s['assets']
   assert s['execution_path_version']==1 and s['account_reads']=='parallel_fresh_get'
   assert s['account']['read_mode']=='parallel_fresh_get'
   assert flags()==flags_before;validate_hashes(deployed)
   services_after={n:service(n) for n in SERVICES};assert all(v['ActiveState']=='active' for v in services_after.values())
   assert all(services_after[n]==services_before[n] for n in SERVICES if n not in (TRADER,UI)),'Other service changed'
   current=rows();assert all(current.get(t)==r for t,r in ledger_before.items()),'Historical rows changed'
   receipt=dict(deployed=True,verified_ts=time.time(),verified_et=datetime.now(ZoneInfo('America/New_York')).isoformat(),backup=str(backup),changed_files=CHANGED,source_hashes=deployed,services_before=services_before,services_after=services_after,config=config_before,flags=flags_before,preserved_historical_records=len(ledger_before),preflight=preflight,tests=191,stable_checkpoint_files=checkpoint_count,benchmark={k:benchmark[k] for k in ('sequential_median_ms','parallel_median_ms','reduction_percent')})
   save(P/'deployment_receipt.json',receipt)
   print('DEPLOYED AND LIVE: fresh parallel account GETs; Qualified/Executable/Submitted/Filled UI; Copper retired with archive; size5/shared25/$25/$20 and entry limits unchanged.',flush=True);return
 raise RuntimeError('Restarted but healthy status unconfirmed; inspect state before further action')
if __name__=='__main__':main()
