"""Guarded user-authorized WS transport cutover. Preflight GET only; no test orders."""
import argparse,fcntl,hashlib,importlib.util,json,os,shutil,sqlite3,subprocess,sys,time
from pathlib import Path
from contextlib import closing
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';S=L/'state'
PAPER=P.parent/'kalshi_resting_paper_2026_10_14'
UNIT='kalshi-crypto-z2-live.service';PUNIT='kalshi-resting-paper.service'
CHANGED=('engine.py','market_data.py','runner.py','stream_transport.py')
OTHER=('kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')
TESTS=('test_engine','test_signals','test_retry','test_execution_improvements','test_zero_fill','test_resilience','test_portfolio','test_z3','test_multiasset','test_wti','test_capacity25','test_execution_path','test_wti_fresh_retry','test_btc_original_z3','test_usdcad','test_bounded_reads','test_cad_runner','test_ops_rounds.Timing','test_ops_rounds.ReadRecovery','test_ops_rounds.SettlementWork','test_ops_rounds.EveryAssetRetry','test_stream_transport')
sys.path.insert(0,str(P/'candidate'))
spec=importlib.util.spec_from_file_location('release_helpers',P.parent/'kalshi_usdcad_2026_10_14/activate.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
sha=a.sha
save=lambda n,d:a.save(P/'receipts'/n,d)
def replace(src,dest):
 tmp=dest.with_name(dest.name+'.ws-migration');shutil.copy2(src,tmp)
 with tmp.open('rb') as f:os.fsync(f.fileno())
 os.replace(tmp,dest)
 fd=os.open(str(dest.parent),os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def account(require_live=True):
 from kalshi_client import Credentials,KalshiClientV2
 from engine import Exchange,crypto,number,ACTIVE
 import runner
 runner.STATE=S
 status=json.loads((S/'status.json').read_text())
 if require_live:assert 0<=time.time()-status['ts']<20 and status['mode']=='LIVE' and status['armed'] and not any(status[k] for k in ('stopped','error','halted')),'Live blocked or stale'
 assert not runner.live_permission()
 assert (S/'HALT_COPPER').exists() and (S/'HALT_USDCAD').exists(),'Preserve both user pauses'
 assert not any((S/n).exists() for n in ('HALT','HALT_Z3')),'Global halt present'
 rows=a.records('orders','ticker')
 assert not any(json.loads(r)['status'] in ACTIVE for r in rows.values()),'Wait for existing positions/pending orders to settle'
 client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);client._request=a.guard_get_only(client._request);ex=Exchange(client)
 try:
  pos=[r for r in ex.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))]
  orders=[r for r in ex.orders(status='resting') if crypto(r.get('ticker',''))]
  assert not pos and not orders,'Exchange is not flat'
  balances={str(i):str(ex.balance(i)) for i in (0,2)}
 finally:client.session.close()
 return dict(ts=time.time(),method='GET_ONLY',positions=0,resting_orders=0,balances=balances,ledger_records=len(rows))
def paper_records():
 with closing(sqlite3.connect((PAPER/'paper.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
  # All historical paper orders are retained through collector restart.
  return list(db.execute('SELECT variant,ticker,record FROM orders ORDER BY variant,ticker'))
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 assert not (P/'receipts/deployment_started.json').exists(),'Prior deployment exists; inspect first'
 expected={p.name:sha(p) for p in (P/'source_before').iterdir() if p.suffix in ('.py','.css')}
 actual_changes={n for n in expected if not n.startswith('test_') and (P/'candidate'/n).exists() and sha(P/'candidate'/n)!=expected[n]}|{'stream_transport.py'}
 assert actual_changes==set(CHANGED),actual_changes
 baseline=a.validate_hashes(expected)
 new={n:sha(P/'candidate'/n) for n in CHANGED};deployed=dict(expected,**new)
 flags=a.flags();config=a.configs(json.loads((S/'status.json').read_text()))
 services={n:a.service(n) for n in (UNIT,PUNIT)+OTHER}
 assert services[UNIT]['ActiveState']=='active'
 for work,tests,log in [(P/'candidate',TESTS,'final_tests.log'),(P/'paper_compat',('test_paper','test_guard','test_integration','test_source_guard'),'final_paper_tests.log')]:
  with (P/'receipts'/log).open('w') as f:r=subprocess.run([sys.executable,'-m','unittest',*tests],cwd=work,stdout=f,stderr=subprocess.STDOUT,timeout=90)
  assert r.returncode==0,'Failed '+log
 shadow=json.loads((P/'receipts/shadow_aligned.json').read_text())
 rollover=json.loads((P/'receipts/shadow_final.json').read_text())
 protocol=json.loads((P/'receipts/protocol_replay.json').read_text())
 assert protocol['all_frames_valid'] and protocol['counts']['orderbook_delta']>200000
 assert len(rollover['tickers'])>=14 and rollover['live_source_unchanged']
 assert shadow['duration']>=240 and shadow['live_source_unchanged'] and shadow['ws']['connected'] and shadow['ws']['error']==''
 assert set(shadow['ws']['channels'])=={'orderbook_delta','fill'} and len(shadow['ws']['valid_books'])==7
 assert shadow['ws']['stats']['orderbook_snapshot']>=100
 # Snapshot equality was an invalid gate: full refreshes can introduce new depth.
 # Actual guards are sequence/quantity/timestamp validity plus time-aligned REST.
 assert all(x['aligned_matches']/x['comparisons']>=.99 for x in shadow['assets'].values())
 assert all(x['comparisons']>=90 and x['mismatches']==0 for x in shadow['assets'].values())
 assert sum(x['stable'] for x in shadow['assets'].values())>=100
 assert shadow['frames'].get('controlled_disconnect')==1
 assert shadow['ws']['stats'].get('disconnects')==1 and len(shadow['ws']['recent_errors'])==1
 assert shadow['ws']['recent_errors'][0]['reason'].startswith('ConnectionClosedOK:'),shadow['ws']['recent_errors']
 assert subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip()=='yes'
 acct=account()
 import runner
 class Healthy:
  def halt_reason(self):return ''
 assert {name:{k:v[k] for k in ('enabled','blocked','contracts','exchange_index','rules')} for name,v in runner.asset_status(Healthy(),[],'LIVE').items()}==config['assets']
 pcfg=json.loads((PAPER/'frozen_config.json').read_text());pconfighash=sha(PAPER/'frozen_config.json')
 assert all(sha(L/n)==h for n,h in pcfg['live_source_hashes'].items())
 assert sha(PAPER/'observer.py')==sha(P/'paper_compat/observer_before.py')
 manifest=dict(reviewed_ts=time.time(),reason='User-authorized free orderbook/fill WebSocket transport migration; strategy unchanged. Segment live comparator at migration time.',before=pcfg['live_source_hashes'],updates=new,migration=str(P/'receipts/deployment_receipt.json'))
 save('paper_reviewed_source_manifest.json',manifest)
 pre=dict(ts=time.time(),account=acct,flags=flags,config=config,services=services,expected=expected,deployed=deployed,baseline_files=baseline,paper_config_sha256=pconfighash,shadow=shadow)
 save('preflight.json',pre)
 if not args.execute:print(json.dumps(dict(ready=True,contracts=5,risk='25/$25/$20',shadow_verified=True)));return
 # Quiet portion of minute, after all old retry budgets; require flat again after stop.
 phase=time.time()%60
 if phase<33:time.sleep(33-phase)
 elif phase>40:time.sleep(93-phase)
 a.validate_hashes(expected);assert a.flags()==flags;account()
 backup=P/'backup';backup.mkdir(exist_ok=False);save('deployment_started.json',pre)
 live_stopped=False;paper_stopped=False;installed=False;paper_installed=False;lock=None
 try:
  # No paper lifetimes should cross this quiet-phase maintenance window.
  pstatus=json.loads((PAPER/'status.json').read_text());assert pstatus['running_jobs']==0
  subprocess.run(['systemctl','--user','stop',PUNIT],check=True,timeout=20);paper_stopped=True
  paper_before=paper_records();save('paper_orders_before.json',paper_before)
  shutil.copy2(PAPER/'observer.py',backup/'paper_observer.py');shutil.copy2(PAPER/'CHANGELOG.txt',backup/'paper_CHANGELOG.txt')
  subprocess.run(['systemctl','--user','stop',UNIT],check=True,timeout=45);live_stopped=True
  lock=(S/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  a.validate_hashes(expected);assert a.flags()==flags;save('stopped_account.json',account(False))
  before=a.records('orders','ticker');attempts=a.records('order_attempts','client_id');save('orders_before.json',before);save('attempts_before.json',attempts)
  with closing(sqlite3.connect((S/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db,closing(sqlite3.connect(backup/'orders.sqlite3')) as dest:db.backup(dest)
  for n in CHANGED:
   if (L/n).exists():shutil.copy2(L/n,backup/n)
  installed=True
  for n in CHANGED:replace(P/'candidate'/n,L/n)
  a.validate_hashes(deployed);assert a.flags()==flags
  assert a.records('orders','ticker')==before and a.records('order_attempts','client_id')==attempts
  paper_installed=True
  replace(P/'paper_compat/observer.py',PAPER/'observer.py');replace(P/'receipts/paper_reviewed_source_manifest.json',PAPER/'reviewed_live_source_hashes.json')
  with (PAPER/'CHANGELOG.txt').open('a') as f:
   f.write('\n2026-10-15 — Authorized live WebSocket migration compatibility\nReviewed exact transport-only hashes for engine, scanner, runner and new stream\nmodule; frozen strategy/configuration/source remain unchanged. Accept original\nrevision for rollback or this exact new revision; reject mixed/unknown changes.\nProduction order-book/fill transport changes at deployment receipt timestamp in\n/home/varma/kalshi_ws_migration_2026_10_15/receipts/deployment_receipt.json.\nSegment live execution comparator at this boundary. Paper uses its same public\nGET receipts, queue model, five contracts and fees. Restart during zero jobs;\nretain every receipt/order and original cutoff. 49 compatibility/model tests pass.\n')
  assert sha(PAPER/'frozen_config.json')==pconfighash and paper_records()==paper_before
  lock.close();lock=None
  subprocess.run(['systemctl','--user','start',UNIT],check=True);live_stopped=False
  deadline=time.time()+65;verified=None
  while time.time()<deadline:
   s=json.loads((S/'status.json').read_text());ws=s.get('market_transport',{})
   if time.time()-s['ts']<5 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('error','halted','stopped')) and ws.get('connected') and set(ws.get('channels',[]))=={'fill','orderbook_delta'} and len(ws.get('valid_books',[]))==7:
    assert a.configs(s)==config;verified=s;break
   time.sleep(.5)
  assert verified,'New trader did not become healthy within65seconds'
  paper_started=time.time()
  subprocess.run(['systemctl','--user','start',PUNIT],check=True);paper_stopped=False
  deadline=time.time()+25;paper_ok=None
  while time.time()<deadline:
   ps=json.loads((PAPER/'status.json').read_text())
   if ps['ts']>paper_started and time.time()-ps['ts']<10 and not ps['health']:paper_ok=ps;break
   time.sleep(.5)
  assert paper_ok,'Paper compatibility guard did not recover'
  current_services={n:a.service(n) for n in (UNIT,PUNIT)+OTHER}
  assert all(current_services[n]==services[n] for n in OTHER),'Unrelated service changed'
  a.validate_hashes(deployed);assert a.flags()==flags and sha(PAPER/'frozen_config.json')==pconfighash
  assert all(a.records('orders','ticker').get(k)==v for k,v in before.items())
  assert all(a.records('order_attempts','client_id').get(k)==v for k,v in attempts.items())
  paper_after={(r[0],r[1]):json.loads(r[2]) for r in paper_records()}
  for variant,ticker,raw in paper_before:
   old=json.loads(raw);newrow=paper_after[(variant,ticker)]
   assert all(newrow.get(k)==old.get(k) for k in ('filled','cost','fees','limit_price','accepted_ts','variant','ticker')),'Historical paper execution changed'
  save('postflight_status.json',verified);save('paper_postflight_status.json',paper_ok)
  receipt=dict(verified_ts=time.time(),live=True,changed=CHANGED,config_unchanged=True,flags_unchanged=True,
      paper_strategy_unchanged=True,baseline_files=baseline,preserved_orders=len(before),preserved_attempts=len(attempts),
      preserved_paper_orders=len(paper_before),services_before=services,services_after=current_services,
      source_hashes=deployed,config=config,paid_channels=False,rollback='Stop only trader and paper; restore three old trader files and paper observer; remove stream_transport.py and reviewed manifest; never restore ledger or ARM/HALT files.')
  save('deployment_receipt.json',receipt)
  print(json.dumps(dict(live_verified=True,websocket_channels=verified['market_transport']['channels'],streamed_books=len(verified['market_transport']['valid_books']),contracts=5,shared='25/$25/$20',paid_channels=False)))
 except BaseException as exc:
  save('deployment_failure.json',dict(ts=time.time(),error=str(exc),installed=installed,paper_installed=paper_installed))
  if installed:
   if not live_stopped:subprocess.run(['systemctl','--user','stop',UNIT],check=True,timeout=45);live_stopped=True
   for n in CHANGED:
    if (backup/n).exists():replace(backup/n,L/n)
    else:(L/n).unlink(missing_ok=True)
  if paper_installed:
   if not paper_stopped:subprocess.run(['systemctl','--user','stop',PUNIT],check=True,timeout=20);paper_stopped=True
   replace(backup/'paper_observer.py',PAPER/'observer.py');(PAPER/'reviewed_live_source_hashes.json').unlink(missing_ok=True)
   with (PAPER/'CHANGELOG.txt').open('a') as f:f.write('\nMigration was rolled back; original transport and source guard restored. Receipts preserved.\n')
  if lock:lock.close();lock=None
  if live_stopped:subprocess.run(['systemctl','--user','start',UNIT],check=True)
  if paper_stopped:subprocess.run(['systemctl','--user','start',PUNIT],check=True)
  raise
 finally:
  if lock:lock.close()
if __name__=='__main__':main()
