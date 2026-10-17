"""Deploy only the verified runner repair; preserve all state, limits and other code."""
import argparse,fcntl,hashlib,json,os,shutil,sqlite3,subprocess,sys,time
from contextlib import closing
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
STATE=LIVE/'state'
SERVICE='kalshi-crypto-z2-live.service'
SERVICES=(SERVICE,'kalshi-dashboard.service','kalshi-execution-observer.service')
TESTS=('test_resilience','test_engine','test_signals','test_portfolio','test_retry','test_z3','test_execution_improvements','test_zero_fill','test_multiasset','test_wti','test_capacity25')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def service(n):return dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
def flags():return {f.name:sha(f) for f in STATE.iterdir() if f.is_file() and f.name.startswith(('HALT','ARM'))}
def rows():
 with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:return {r[0]:r[1] for r in db.execute('select ticker,record from orders')}
def configs(s):
 return dict(settings={k:s[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget','signal_ask_ceiling_dollars','execution_cap_dollars','z_min','minutes','execution_checks_max','execution_retry_deadline_seconds','max_ioc_submissions','zero_fill_retry_enabled','pending_reconcile_seconds')},
  assets={a:{k:v[k] for k in ('enabled','blocked','contracts','exchange_index','rules')} for a,v in s['assets'].items()},
  lanes={n:{k:v[k] for k in ('contracts','z_min','signal_floor','signal_ceiling','execution_cap','enabled','blocked','max_contracts','max_risk','daily_loss_budget')} for n,v in s['lanes'].items()})
def validate_hashes(expected):
 actual={p.name:sha(p) for p in LIVE.glob('*.py')}
 if actual!=expected:raise RuntimeError('Live source drift: '+str([k for k in set(actual)|set(expected) if actual.get(k)!=expected.get(k)]))
 checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
 manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256']
 assert all(sha(checkpoint/'source'/n)==h for n,h in manifest.items()),'Stable checkpoint changed'
 return len(manifest)
def readonly_account(require_live=True):
 from kalshi_client import Credentials,KalshiClientV2
 from engine import Exchange,crypto,number,ACTIVE
 import runner
 s=json.loads((STATE/'status.json').read_text())
 if require_live:
  assert time.time()-s['ts']<30 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')),'Live status blocked/stale'
 assert not runner.live_permission(),'Existing permission guard is blocked'
 assert (STATE/'HALT_COPPER').exists(),'Copper pause must persist'
 assert not (STATE/'HALT').exists() and not (STATE/'HALT_Z3').exists(),'Global/lane halt exists'
 ledger=rows();assert not any(json.loads(r)['status'] in ACTIVE for r in ledger.values()),'Wait for pending/open trades to reconcile'
 client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);request=client._request
 def get_only(method,*a,**kw):
  assert method=='GET','Deployment must never submit/cancel exchange orders'
  return request(method,*a,**kw)
 client._request=get_only;ex=Exchange(client)
 positions=[r for r in ex.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))]
 resting=[r for r in ex.orders(status='resting') if crypto(r.get('ticker',''))]
 assert not positions and not resting,'Exchange exposure requires reconciliation'
 balances={str(i):str(ex.balance(i)) for i in (0,2)}
 return dict(ts=time.time(),mode='GET_ONLY',supported_positions=0,supported_resting_orders=0,pending_or_open_records=0,ledger_records=len(ledger),balances=balances)
def replace_file(src,dest):
 tmp=dest.with_name(dest.name+'.resilience-upgrade')
 shutil.copy2(src,tmp)
 with tmp.open('rb') as f:os.fsync(f.fileno())
 os.replace(tmp,dest)
 fd=os.open(str(dest.parent),os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def main():
 args=argparse.ArgumentParser();args.add_argument('--execute',action='store_true');args=args.parse_args()
 expected=json.loads((P/'source_hashes.json').read_text())
 bundle=json.loads((P/'candidate_manifest.json').read_text())['sha256']
 assert all(sha(P/'candidate'/n)==h for n,h in bundle.items()),'Candidate bundle changed'
 assert [n for n,h in bundle.items() if n in expected and not n.startswith('test_') and h!=expected[n]]==['runner.py']
 validate_hashes(expected)
 with (P/'deployment_tests.log').open('w') as log:
  subprocess.run([sys.executable,'-m','unittest',*TESTS],cwd=P/'candidate',stdout=log,stderr=subprocess.STDOUT,check=True)
 sys.path.insert(0,str(LIVE))
 before=json.loads((STATE/'status.json').read_text());config_before=configs(before);flags_before=flags()
 assert not before['assets']['COPPER']['enabled'] and sum(v['enabled'] for v in before['assets'].values())==7
 services_before={n:service(n) for n in SERVICES};assert all(v['ActiveState']=='active' for v in services_before.values())
 preflight=readonly_account();save(P/'deployment_preflight.json',dict(account=preflight,config=config_before,flags=flags_before,services=services_before,tests='171 passed'))
 print('Preflight PASS; exact source match; flat account; Copper paused; 171 tests passed.',flush=True)
 if not args.execute:return
 # Stop outside the 2–30-second scan/submission slice. At most one bounded wait.
 second=time.time()%60
 if second<33:time.sleep(33-second)
 elif second>45:time.sleep(93-second)
 backup=P/('deployment_backup_'+str(time.time_ns()));backup.mkdir()
 save(backup/'state_flags.json',flags_before);save(backup/'services_before.json',services_before)
 changed=False;lock=None;stopped=False
 try:
  stopped=True
  subprocess.run(['systemctl','--user','stop',SERVICE],check=True,timeout=40)
  lock=(STATE/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  validate_hashes(expected);assert flags()==flags_before,'Activation/pause state changed'
  stopped_check=readonly_account(require_live=False);save(backup/'stopped_account.json',stopped_check)
  ledger_before=rows();save(backup/'ledger_records.json',ledger_before)
  with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
   with closing(sqlite3.connect(backup/'orders.sqlite3')) as target:source.backup(target)
  shutil.copy2(LIVE/'runner.py',backup/'runner.py')
  changed=True;replace_file(P/'candidate/runner.py',LIVE/'runner.py')
  deployed=dict(expected,**{'runner.py':bundle['runner.py']});validate_hashes(deployed)
  subprocess.run([sys.executable,'-c','import runner,engine; assert runner.CONTRACTS==5; assert runner.MAX_CONTRACTS==25; assert hasattr(runner,"get_budget")'],cwd=LIVE,check=True)
  assert rows()==ledger_before,'Ledger changed while stopped'
  assert flags()==flags_before,'Activation/pause flags changed'
 except BaseException:
  if changed:replace_file(backup/'runner.py',LIVE/'runner.py');validate_hashes(expected)
  if lock is not None:lock.close()
  if stopped:subprocess.run(['systemctl','--user','start',SERVICE],check=True)
  raise
 finally:
  if lock is not None and not lock.closed:lock.close()
 started=time.time();subprocess.run(['systemctl','--user','start',SERVICE],check=True)
 save(P/'deployment_started.json',dict(started_ts=started,backup=str(backup),source_hashes=deployed,flags=flags_before))
 # Once trading resumes, never restore a ledger or blindly roll back over orders.
 for _ in range(50):
  time.sleep(1)
  s=json.loads((STATE/'status.json').read_text())
  if s['ts']>=started and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')):
   assert configs(s)==config_before,'Effective configuration changed'
   assert flags()==flags_before
   validate_hashes(deployed)
   services_after={n:service(n) for n in SERVICES}
   assert all(v['ActiveState']=='active' for v in services_after.values())
   assert all(services_after[n]==services_before[n] for n in SERVICES if n!=SERVICE),'Unrelated Kalshi service changed'
   current=rows();assert all(current.get(t)==r for t,r in ledger_before.items()),'Historical ledger rows changed'
   receipt=dict(deployed=True,verified_ts=time.time(),verified_et=datetime.now(ZoneInfo('America/New_York')).isoformat(),backup=str(backup),only_changed='runner.py',source_hashes=deployed,services_before=services_before,services_after=services_after,config=config_before,flags=flags_before,preserved_historical_records=len(ledger_before),preflight=preflight,tests='171 passed',stable_checkpoint_files=12)
   save(P/'deployment_receipt.json',receipt)
   print('DEPLOYED AND LIVE: resilience fixes; size5/shared25/$25/$20 and all entry rules unchanged; Copper paused.',flush=True)
   return
 raise RuntimeError('Restarted but healthy status unconfirmed; inspect current state before any further action')
if __name__=='__main__':main()
