"""Guarded user-authorized fill-confirmation repair. No financial writes in this script."""
import argparse,ast,fcntl,hashlib,importlib.util,json,os,shutil,sqlite3,subprocess,sys,time
from contextlib import closing
from pathlib import Path
P=Path(__file__).resolve().parent
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';S=L/'state'
PAPER=P.parent/'kalshi_resting_paper_2026_10_14'
CHANGED=('stream_transport.py','engine.py','runner.py')
UNIT='kalshi-crypto-z2-live.service';PUNIT='kalshi-resting-paper.service'
OTHER=('kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')
sys.path.insert(0,str(P/'candidate'))
spec=importlib.util.spec_from_file_location('release_helpers',P.parent/'kalshi_usdcad_2026_10_14/activate.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
sha=a.sha
def save(name,value):a.save(P/'receipts'/name,value)
def replace(src,dest):
    tmp=dest.with_name(dest.name+'.fill-fix');shutil.copy2(src,tmp)
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
    s=json.loads((S/'status.json').read_text())
    if require_live:assert 0<=time.time()-s['ts']<20 and s['armed'] and s['mode']=='LIVE' and not any(s[k] for k in ('stopped','error','halted'))
    assert not runner.live_permission()
    assert (S/'HALT_COPPER').exists() and (S/'HALT_USDCAD').exists()
    rows=a.records('orders','ticker')
    assert not any(json.loads(r)['status'] in ACTIVE for r in rows.values()),'Wait for open/pending trades to settle; not changed'
    client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
    client._request=a.guard_get_only(client._request);ex=Exchange(client)
    try:
        positions=[r for r in ex.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))]
        resting=[r for r in ex.orders(status='resting') if crypto(r.get('ticker',''))]
        assert not positions and not resting,'Exchange not flat'
        cash={str(i):str(ex.balance(i)) for i in (0,2)}
    finally:client.session.close()
    return dict(ts=time.time(),method='GET_ONLY',positions=0,resting_orders=0,cash=cash)
def paper_records():
    with closing(sqlite3.connect((PAPER/'paper.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        return list(db.execute('SELECT variant,ticker,record FROM orders ORDER BY variant,ticker'))
def unchanged_book_logic():
    def node(path,name):
        return next(n for n in ast.parse(path.read_text()).body if getattr(n,'name',None)==name)
    for name in ('BookState','subscription','decimal','allowed'):
        assert ast.dump(node(P/'candidate/stream_transport.py',name))==ast.dump(node(P/'source_before/stream_transport.py',name))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');args=ap.parse_args()
    assert not (P/'receipts/deployment_started.json').exists(),'Prior deployment: inspect before retry'
    prior=json.loads((P.parent/'kalshi_ws_migration_2026_10_15/receipts/deployment_receipt.json').read_text())
    expected=prior['source_hashes'];baseline=a.validate_hashes(expected)
    assert {n for n in expected if (P/'candidate'/n).exists() and not n.startswith('test_') and sha(P/'candidate'/n)!=expected[n]}==set(CHANGED)
    bundle=json.loads((P/'candidate_manifest.json').read_text())
    assert all(sha(P/'candidate'/n)==h for n,h in bundle.items())
    unchanged_book_logic()
    tests=json.loads((P/'test_command.json').read_text())
    with (P/'receipts/tests.log').open('w') as out:
        result=subprocess.run([sys.executable,'-m','unittest',*tests],cwd=P/'candidate',stdout=out,stderr=subprocess.STDOUT,timeout=90)
    assert result.returncode==0,'Tests failed'
    print('OFFLINE TESTS PASSED',flush=True)
    config=a.configs(json.loads((S/'status.json').read_text()));flags=a.flags()
    services={n:a.service(n) for n in (UNIT,PUNIT)+OTHER}
    assert services[UNIT]['ActiveState']=='active' and services[PUNIT]['ActiveState']=='active'
    acct=account();new={n:sha(P/'candidate'/n) for n in CHANGED};deployed=dict(expected,**new)
    manifest_before=json.loads((PAPER/'reviewed_live_source_hashes.json').read_text())
    manifest=dict(manifest_before,reviewed_ts=time.time(),updates=dict(manifest_before['updates'],**new),
                  reason='User-authorized canonical fill confirmation repair; trading and paper parameters unchanged.',
                  fill_repair=str(P/'receipts/deployment_receipt.json'))
    paper_cfg_hash=sha(PAPER/'frozen_config.json');paper_source=sha(PAPER/'observer.py')
    save('paper_manifest.json',manifest)
    pre=dict(ts=time.time(),config=config,flags=flags,services=services,account=acct,source_before=expected,
             source_after=deployed,baseline_files=baseline,paper_config_hash=paper_cfg_hash,paper_source_hash=paper_source)
    save('preflight.json',pre)
    print(json.dumps(dict(ready=True,contracts=5,risk='25/$25/$20',changed=CHANGED)),flush=True)
    if not args.execute:return
    # Quiet phase only; check flat both before stopping and under process lock.
    phase=time.time()%60
    if phase<34:time.sleep(34-phase)
    elif phase>44:time.sleep(94-phase)
    a.validate_hashes(expected);assert a.flags()==flags;account()
    assert json.loads((PAPER/'status.json').read_text())['running_jobs']==0
    backup=P/'backup';backup.mkdir(exist_ok=False)
    save('deployment_started.json',dict(ts=time.time(),preflight=pre))
    installed=False;stopped=[];lock=None
    try:
        for unit in (PUNIT,UNIT):
            stopped.append(unit);subprocess.run(['systemctl','--user','stop',unit],check=True,timeout=45)
        lock=(S/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        a.validate_hashes(expected);assert a.flags()==flags;save('stopped_account.json',account(False))
        before=a.records('orders','ticker');attempts=a.records('order_attempts','client_id');paper_before=paper_records()
        save('orders_before.json',before);save('attempts_before.json',attempts);save('paper_orders_before.json',paper_before)
        with closing(sqlite3.connect((S/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(backup/'orders.sqlite3')) as dest:src.backup(dest)
        for n in CHANGED:shutil.copy2(L/n,backup/n)
        shutil.copy2(PAPER/'reviewed_live_source_hashes.json',backup/'paper_manifest.json')
        installed=True
        for n in CHANGED:replace(P/'candidate'/n,L/n)
        replace(P/'receipts/paper_manifest.json',PAPER/'reviewed_live_source_hashes.json')
        a.validate_hashes(deployed)
        assert a.records('orders','ticker')==before and a.records('order_attempts','client_id')==attempts
        assert a.flags()==flags and paper_records()==paper_before
        lock.close();lock=None
        subprocess.run(['systemctl','--user','start',UNIT],check=True)
        started=time.time();good=None
        while time.time()-started<45:
            s=json.loads((S/'status.json').read_text());w=s.get('market_transport',{})
            if s['ts']>started and time.time()-s['ts']<6 and s['armed'] and not any(s[k] for k in ('error','halted','stopped')) and w.get('fill_validation_version')==2 and w.get('connected') and set(w.get('channels',[]))=={'fill','orderbook_delta'} and len(w.get('valid_books',[]))==7:
                assert a.configs(s)==config;good=s;break
            time.sleep(.5)
        assert good,'New service not healthy'
        subprocess.run(['systemctl','--user','start',PUNIT],check=True);started=time.time();paper_good=None
        while time.time()-started<20:
            ps=json.loads((PAPER/'status.json').read_text())
            if ps['ts']>started and not ps['health']:paper_good=ps;break
            time.sleep(.5)
        assert paper_good,'Paper source guard unhealthy'
        a.validate_hashes(deployed);assert a.flags()==flags
        assert sha(PAPER/'frozen_config.json')==paper_cfg_hash and sha(PAPER/'observer.py')==paper_source
        current={n:a.service(n) for n in (UNIT,PUNIT)+OTHER}
        assert all(current[n]==services[n] for n in OTHER),'Unrelated service changed'
        assert all(a.records('orders','ticker').get(k)==v for k,v in before.items())
        assert all(a.records('order_attempts','client_id').get(k)==v for k,v in attempts.items())
        save('postflight_status.json',good)
        save('deployment_receipt.json',dict(verified_ts=time.time(),source_hashes=deployed,changed=CHANGED,
             config=config,config_unchanged=True,flags_unchanged=True,baseline_files=baseline,
             preserved_orders=len(before),preserved_attempts=len(attempts),preserved_paper_orders=len(paper_before),
             services_before=services,services_after=current,actual_ws_fills_verified=False))
        print('LIVE REPAIR INSTALLED: canonical fill validation; unchanged strategy, prices, size5, shared25/$25/$20. Awaiting actual fill verification.',flush=True)
    except BaseException as exc:
        if installed:
            for unit in (PUNIT,UNIT):subprocess.run(['systemctl','--user','stop',unit],check=True,timeout=45)
            for n in CHANGED:replace(backup/n,L/n)
            replace(backup/'paper_manifest.json',PAPER/'reviewed_live_source_hashes.json')
            a.validate_hashes(expected)
        if lock is not None:lock.close();lock=None
        for unit in reversed(stopped):subprocess.run(['systemctl','--user','start',unit],check=True)
        save('deployment_failure.json',dict(ts=time.time(),error=repr(exc),code_restored=installed,ledger_never_restored=True))
        raise
    finally:
        if lock is not None:lock.close()
if __name__=='__main__':main()
