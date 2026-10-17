"""User-run USD/CAD activation. Default mode performs only reads and offline tests."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from urllib.request import urlopen

P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';STATE=LIVE/'state'
CHANGED=('lanes.py','dashboard_panel.py')
ARM='ARM_USDCAD_5CT'
RESTART=('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service')
PRESERVE=('kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def service(n):
    return dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
def flags():return {f.name:sha(f) for f in STATE.iterdir() if f.is_file() and f.name.startswith(('HALT','ARM'))}
def records(table,key):
    assert (table,key) in (('orders','ticker'),('order_attempts','client_id'))
    with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        return dict(db.execute(f'SELECT {key},record FROM {table}'))
def configs(s):
    return dict(settings={k:s[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget','signal_ask_ceiling_dollars','execution_cap_dollars','z_min','minutes','execution_checks_max','execution_retry_deadline_seconds','max_ioc_submissions','zero_fill_retry_enabled','pending_reconcile_seconds')},
        assets={a:{k:v[k] for k in ('enabled','blocked','contracts','exchange_index','rules')} for a,v in s['assets'].items()},
        lanes={n:{k:v[k] for k in ('contracts','z_min','signal_floor','signal_ceiling','execution_cap','enabled','blocked','max_contracts','max_risk','daily_loss_budget')} for n,v in s['lanes'].items()})
def validate_hashes(expected):
    actual={p.name:sha(p) for p in LIVE.iterdir() if p.suffix=='.py' or p.name=='dashboard.css'}
    assert actual==expected,'Production changed since preparation; review before installing'
    checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
    manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256']
    assert all(sha(checkpoint/'source'/n)==h for n,h in manifest.items()),'Stable checkpoint changed'
    return len(manifest)
def guard_get_only(request):
    def get_only(method,*a,**kw):
        if method!='GET':raise RuntimeError('Preflight cannot submit/cancel/transfer')
        return request(method,*a,**kw)
    return get_only
def readonly_account(require_live=True):
    from kalshi_client import Credentials,KalshiClientV2
    from engine import Exchange,crypto,number,ACTIVE
    import runner
    # Candidate imports must never point their state at the candidate directory.
    runner.STATE=STATE
    s=json.loads((STATE/'status.json').read_text())
    if require_live:
        assert 0<=time.time()-s['ts']<30 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')),'Existing bot is blocked or stale'
    assert not runner.live_permission(),'Existing permission guard blocked'
    assert (STATE/'HALT_COPPER').exists(),'Copper halt must remain'
    assert not any((STATE/n).exists() for n in ('HALT','HALT_Z3','HALT_USDCAD')),'Manual halt present'
    ledger=records('orders','ticker')
    assert not any(json.loads(r)['status'] in ACTIVE for r in ledger.values()),'Open/pending trade: let it settle, then rerun; services untouched'
    client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
    client._request=guard_get_only(client._request);ex=Exchange(client)
    try:
        positions=[r for r in ex.positions() if crypto(r.get('ticker','')) and abs(number(r,'position_fp','position'))]
        resting=[r for r in ex.orders(status='resting') if crypto(r.get('ticker',''))]
        assert not positions and not resting,'Open account exposure: let it reconcile, then rerun'
        balances={str(i):str(ex.balance(i)) for i in (0,2)}
        assert float(balances['0'])>=10 and float(balances['2'])>=10,'Require $10 free on each active funding route'
    finally:client.session.close()
    return dict(ts=time.time(),mode='GET_ONLY',supported_positions=0,supported_resting_orders=0,
                pending_or_open_records=0,ledger_records=len(ledger),balances=balances)
def replace_file(src,dest):
    tmp=dest.with_name(dest.name+'.usdcad-upgrade');shutil.copy2(src,tmp)
    with tmp.open('rb') as f:os.fsync(f.fileno())
    os.replace(tmp,dest)
    fd=os.open(str(dest.parent),os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def expected_configuration(before):
    import runner
    runner.STATE=STATE
    class Healthy:
        def halt_reason(self):return ''
    candidate=runner.asset_status(Healthy(),[],'LIVE')
    after=copy.deepcopy(before)
    assert set(candidate)==set(before['assets'])|{'USDCAD'}
    for a in before['assets']:
        assert {k:candidate[a][k] for k in before['assets'][a]}==before['assets'][a],f'Unexpected existing profile change: {a}'
    after['assets']['USDCAD']={k:candidate['USDCAD'][k] for k in ('enabled','blocked','contracts','exchange_index','rules')}
    after['assets']['USDCAD'].update(enabled=True,blocked='')
    return after
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    assert not (P/'deployment_receipt.json').exists(),'Already activated; inspect deployment_receipt.json'
    assert not (P/'deployment_started.json').exists(),'Prior activation started; inspect live state before retrying'
    expected=json.loads((P/'original_hashes.json').read_text());bundle=json.loads((P/'candidate_manifest.json').read_text())
    assert all(sha(P/'candidate'/n)==h for n,h in bundle.items()),'Candidate changed since review'
    assert set(CHANGED)=={n for n,h in bundle.items() if not n.startswith('test_') and expected.get(n)!=h},'Unexpected production changes'
    assert not (STATE/ARM).exists(),'USD/CAD already armed; inspect before changing'
    checkpoint_count=validate_hashes(expected)
    command=json.loads((P/'test_command.json').read_text())['argv']
    with (P/'activation_tests.log').open('w') as out:
        result=subprocess.run(command,cwd=P/'candidate',stdout=out,stderr=subprocess.STDOUT)
    assert result.returncode==0,'Offline tests failed: see activation_tests.log'
    print('OFFLINE TESTS PASSED (activation_tests.log)',flush=True)
    sys.path.insert(0,str(P/'candidate'))
    from source_probe import probe
    source=probe();save(P/'source_probe.json',source)
    services_before={n:service(n) for n in RESTART+PRESERVE}
    assert all(services_before[n]['ActiveState']=='active' for n in RESTART),'Required service not active'
    flags_before=flags();before=configs(json.loads((STATE/'status.json').read_text()))
    after=expected_configuration(before)
    preflight=readonly_account()
    save(P/'deployment_preflight.json',dict(ts=time.time(),activated=False,account=preflight,source=source['evidence'],config_before=before,config_after=after,flags=flags_before,services=services_before,stable_checkpoint_files=checkpoint_count))
    print(json.dumps(dict(mode='CHECK_ONLY',ready=True,activated=False,new_asset='USDCAD',contracts=5,exchange_index=0,
        free_fx_cash=preflight['balances']['0'],shared_max_contracts=25,shared_exposure='25',shared_daily_loss='20',source_bars=61)),flush=True)
    if not args.execute:return
    # Prefer the quiet part of a minute and recheck after stopping. No orders are
    # canceled and no ledger is altered by this deployment script.
    second=time.time()%60
    if second<33:time.sleep(33-second)
    elif second>45:time.sleep(93-second)
    backup=P/('deployment_backup_'+str(time.time_ns()));backup.mkdir()
    save(backup/'flags.json',flags_before);save(backup/'services.json',services_before)
    modified=[];lock=None;stopped=[];armed=False
    try:
        for unit in RESTART:
            stopped.append(unit);subprocess.run(['systemctl','--user','stop',unit],check=True,timeout=40)
        lock=(STATE/'process.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        validate_hashes(expected);assert flags()==flags_before
        save(backup/'account.json',readonly_account(require_live=False))
        ledger_before=records('orders','ticker');attempts_before=records('order_attempts','client_id')
        save(backup/'ledger_records.json',ledger_before);save(backup/'attempt_records.json',attempts_before)
        with closing(sqlite3.connect((STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as source_db:
            with closing(sqlite3.connect(backup/'orders.sqlite3')) as dest_db:source_db.backup(dest_db)
        for name in CHANGED:shutil.copy2(LIVE/name,backup/name)
        for name in CHANGED:
            modified.append(name);replace_file(P/'candidate'/name,LIVE/name)
        deployed=dict(expected,**{n:bundle[n] for n in CHANGED});validate_hashes(deployed)
        subprocess.run([sys.executable,'-c','import runner,lanes,market_data,dashboard_panel; assert len(lanes.ACTIVE_ASSETS)==8; assert "USDCAD" in market_data.ASSETS; assert "AUDUSD" not in market_data.ASSETS; assert "COPPER" not in market_data.ASSETS; assert lanes.route_for(dict(asset="USDCAD"))==0; assert lanes.lane_for(dict(asset="BTC")).z==3; assert lanes.lane_for(dict(asset="USDCAD")).z_slope==.5; assert runner.CONTRACTS==5; assert runner.MAX_CONTRACTS==25; assert runner.MAX_RISK==25; assert runner.DAILY_LOSS_BUDGET==20'],cwd=LIVE,check=True)
        assert records('orders','ticker')==ledger_before and flags()==flags_before
        marker=backup/'new_arm';marker.write_text('5\n');armed=True;replace_file(marker,STATE/ARM)
        flags_after=dict(flags_before,**{ARM:sha(marker)});assert flags()==flags_after
    except BaseException:
        if armed:(STATE/ARM).unlink(missing_ok=True)
        for name in reversed(modified):replace_file(backup/name,LIVE/name)
        validate_hashes(expected)
        if lock is not None:lock.close()
        for unit in stopped:subprocess.run(['systemctl','--user','start',unit],check=True)
        raise
    finally:
        if lock is not None and not lock.closed:lock.close()
    started=time.time()
    # Save before starting: an interrupted restart must not permit blind reactivation.
    save(P/'deployment_started.json',dict(started_ts=started,backup=str(backup),source_hashes=deployed,flags=flags_after))
    for unit in RESTART:subprocess.run(['systemctl','--user','start',unit],check=True)
    for _ in range(55):
        time.sleep(1);s=json.loads((STATE/'status.json').read_text())
        if s['ts']<started or s['mode']!='LIVE' or not s['armed'] or any(s[k] for k in ('stopped','error','halted')):continue
        assert configs(s)==after,'Effective configuration differs from authorized USD/CAD addition'
        assert s['execution_path_version']==1 and s['wti_fresh_retry_version']==1
        assert flags()==flags_after;validate_hashes(deployed)
        services_after={n:service(n) for n in RESTART+PRESERVE}
        assert all(services_after[n]['ActiveState']=='active' for n in RESTART)
        assert all(services_after[n]==services_before[n] for n in PRESERVE),'Unrelated paper service changed'
        current=records('orders','ticker');attempts=records('order_attempts','client_id')
        assert all(current.get(t)==r for t,r in ledger_before.items()),'Historical ledger changed'
        assert all(attempts.get(t)==r for t,r in attempts_before.items()),'Historical attempts changed'
        with urlopen('http://192.168.3.136:8507/_stcore/health',timeout=5) as response:assert response.status==200
        receipt=dict(activated=True,verified_ts=time.time(),backup=str(backup),changed_files=CHANGED,
            source_hashes=deployed,config_before=before,config_after=after,flags=flags_after,
            services_before=services_before,services_after=services_after,preserved_records=len(ledger_before),
            preserved_attempts=len(attempts_before),stable_checkpoint_files=checkpoint_count,preflight=preflight)
        save(P/'deployment_receipt.json',receipt)
        print('LIVE VERIFIED: USD/CAD added, 5 contracts per entry; adaptive original1.75/premium2.75 at5min; shared25/$25/$20. Existing lanes, price caps and retry rules unchanged. AUD/USD remains deferred.',flush=True)
        return
    raise RuntimeError('Activation started but healthy status unconfirmed; inspect live state and deployment_started.json before further action')

if __name__=='__main__':main()
