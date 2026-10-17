"""Deploy the five-round operational latency and retry repair."""
import argparse,fcntl,hashlib,importlib.util,json,os,shutil,sqlite3,subprocess,sys,time
from pathlib import Path
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';STATE=L/'state'
CHANGED=('runner.py',)
UNIT='kalshi-crypto-z2-live.service'
SERVICES=(UNIT,'kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')
TESTS=('test_engine','test_signals','test_retry','test_execution_improvements','test_zero_fill','test_resilience','test_portfolio','test_z3','test_multiasset','test_wti','test_capacity25','test_execution_path','test_wti_fresh_retry','test_btc_original_z3','test_usdcad','test_bounded_reads','test_cad_runner','test_ops_rounds.Timing','test_ops_rounds.ReadRecovery','test_ops_rounds.SettlementWork','test_ops_rounds.EveryAssetRetry')
sys.path.insert(0,str(P/'candidate'))
spec=importlib.util.spec_from_file_location('previous_release_helpers',P.parent/'kalshi_usdcad_2026_10_14/activate.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
def save(name,data):a.save(P/('phase3_'+name),data)
def replace(src,dest):
 tmp=dest.with_name(dest.name+'.read-retry-update');shutil.copy2(src,tmp)
 with tmp.open('rb') as f:os.fsync(f.fileno())
 os.replace(tmp,dest)
 fd=os.open(str(dest.parent),os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 assert not (P/'phase3_deployment_receipt.json').exists(),'Deployment already complete'
 assert not (P/'phase3_deployment_started.json').exists(),'Inspect earlier deployment before retrying'
 expected=json.loads((P/'phase3_original_hashes.json').read_text())
 changed=tuple(sorted(n for n in expected if n.endswith('.py') and not n.startswith('test_') and a.sha(P/'candidate'/n)!=expected[n]))
 assert changed==tuple(sorted(CHANGED)),changed
 for n in CHANGED:assert a.sha(P/'phase3_original'/n)==expected[n]
 checkpoint_files=a.validate_hashes(expected)
 flags=a.flags();services={n:a.service(n) for n in SERVICES}
 assert services[UNIT]['ActiveState']=='active'
 config=a.configs(json.loads((STATE/'status.json').read_text()))
 with (P/'phase3_final_tests.log').open('w') as log:
  result=subprocess.run([sys.executable,'-m','unittest',*TESTS],cwd=P/'candidate',stdout=log,stderr=subprocess.STDOUT,timeout=60)
 assert result.returncode==0,'Offline tests failed; no production changes'
 account=a.readonly_account()
 import runner
 class Healthy:
  def halt_reason(self):return ''
 assert {name:{k:v[k] for k in ('enabled','blocked','contracts','exchange_index','rules')} for name,v in runner.asset_status(Healthy(),[],'LIVE').items()}==config['assets']
 pre=dict(ts=time.time(),account=account,config=config,flags=flags,services=services,checkpoint_files=checkpoint_files,changed=CHANGED,candidate={n:a.sha(P/'candidate'/n) for n in CHANGED})
 save('preflight.json',pre)
 if not args.execute:
  print(json.dumps(dict(mode='CHECK_ONLY',ready=True,contracts=config['settings']['contracts'],account_deadline=3,fresh_retry_assets=list(runner.market_data.ASSETS))));return
 # All asset entry windows are closed during the first four minutes of each quarter.
 phase=time.time()%900
 assert 7<=phase<=285,'Wait for the first four minutes of a quarter-hour; service untouched'
 a.validate_hashes(expected);assert a.flags()==flags
 save('deployment_started.json',pre)
 stopped=False;installed=False;lock=None;backup=P/'phase3_backup';backup.mkdir()
 try:
  subprocess.run(['systemctl','--user','stop',UNIT],check=True,timeout=65);stopped=True
  assert a.service(UNIT)['ActiveState']=='inactive'
  lock=(STATE/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  a.validate_hashes(expected);assert a.flags()==flags
  stopped_account=a.readonly_account(require_live=False)
  before=a.records('orders','ticker');attempts=a.records('order_attempts','client_id')
  with sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup/'orders.sqlite3') as target:source.backup(target)
  save('preserved_orders.json',before);save('preserved_attempts.json',attempts)
  for n in CHANGED:shutil.copy2(L/n,backup/n)
  save('stopped_preflight.json',stopped_account)
  installed=True
  for n in CHANGED:replace(P/'candidate'/n,L/n)
  assert a.flags()==flags
  assert a.records('orders','ticker')==before and a.records('order_attempts','client_id')==attempts
  assert all(a.sha(L/n)==h for n,h in expected.items() if n not in CHANGED)
  lock.close();lock=None
  subprocess.run(['systemctl','--user','start',UNIT],check=True);stopped=False
  deadline=time.time()+45;verified=None
  while time.time()<deadline:
   s=json.loads((STATE/'status.json').read_text())
   if s.get('execution_path_version')==2 and s.get('fresh_retry_version')==2 and time.time()-s['ts']<5 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('error','halted','stopped')):
    assert a.configs(s)==config,'Runtime strategy/limits changed'
    assert s['account_read_deadline_seconds']==3 and s['fresh_retry_assets']==list(runner.market_data.ASSETS) and s['scan_start_seconds']==.25
    verified=s;break
   time.sleep(.5)
  assert verified,'Runtime postflight not healthy within45seconds'
  after_services={n:a.service(n) for n in SERVICES}
  assert all(after_services[n]==services[n] for n in SERVICES if n!=UNIT)
  assert a.flags()==flags
  assert all(a.records('orders','ticker').get(k)==v for k,v in before.items())
  assert all(a.records('order_attempts','client_id').get(k)==v for k,v in attempts.items())
  assert all(a.sha(L/n)==h for n,h in expected.items() if n not in CHANGED)
  a.validate_hashes(dict(expected,**{n:a.sha(P/'candidate'/n) for n in CHANGED}))
  save('postflight_status.json',verified)
  save('deployment_receipt.json',dict(ts=time.time(),changed=CHANGED,services_before=services,services_after=after_services,flags_unchanged=True,config_unchanged=True,checkpoint_files=checkpoint_files,preserved_orders=len(before),preserved_attempts=len(attempts),sha256={n:a.sha(L/n) for n in CHANGED},rollback='Restore only the five code files in backup; never restore the live ledger'))
  print(json.dumps(dict(live_verified=True,account_deadline_seconds=3,fresh_retry_assets=list(runner.market_data.ASSETS),contracts=5,max_contracts=25,max_risk='25',daily_loss_budget='20',trader_pid=after_services[UNIT]['MainPID'],other_service_pids_unchanged=True)))
 except BaseException as exc:
  save('deployment_failure.json',dict(ts=time.time(),error=str(exc),installed=installed,stopped=stopped))
  if installed:
   if not stopped:subprocess.run(['systemctl','--user','stop',UNIT],check=True,timeout=65);stopped=True
   for n in CHANGED:replace(backup/n,L/n)
  if lock:lock.close();lock=None
  if stopped:subprocess.run(['systemctl','--user','start',UNIT],check=True)
  raise
 finally:
  if lock:lock.close()
if __name__=='__main__':main()
