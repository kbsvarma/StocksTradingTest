"""Check the execution-only upgrade by default; explicit --execute installs it."""
import argparse
from contextlib import closing
import os
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
SERVICE='kalshi-crypto-z2-live.service'
FILES=('engine.py','runner.py','execution_retry.py','dashboard_panel.py')
ACTIVE=('intent','unknown','ack','open','accounting_pending')
Z3_ARM='ARM_Z3_5CT'

def verify_hashes(root,expected):
    allowed={'engine.py','market_data.py','runner.py','dashboard_panel.py','lanes.py','sizing.py','execution_retry.py','kalshi_client.py'}
    for name,wanted in expected.items():
        if name not in allowed:raise RuntimeError('Unexpected manifest path')
        path=root/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:
            raise RuntimeError(str(path)+' changed; refresh staging before activation')

def live_readiness(require_heartbeat=True):
    from kalshi_client import KalshiClientV2,Credentials
    from engine import Exchange,crypto,number
    from runner import legacy_safe
    state=LIVE/'state'
    for name in ('HALT','HALT_Z3'):
        if (state/name).exists():raise RuntimeError(name+' exists; preserved')
    if (state/'ARM_5CT').read_text().strip()!='5':raise RuntimeError('Existing z2 arm invalid')
    if (state/Z3_ARM).read_text().strip()!='5':raise RuntimeError('Existing z3 arm invalid')
    if (state/'ARM_Z3_1CT').exists():raise RuntimeError('An older one-contract z3 arm exists; review deployment before activating five contracts')
    reason=legacy_safe()
    if reason:raise RuntimeError(reason)
    if require_heartbeat:
        d=json.loads((state/'status.json').read_text())
        if not 0<=time.time()-d.get('ts',0)<=45 or d.get('mode')!='LIVE' or not d.get('armed') or any(d.get(k) for k in ('stopped','error','halted')):
            raise RuntimeError('Existing bot heartbeat is stale or blocked')
    conn=sqlite3.connect((state/'orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)
    try:rows=[json.loads(r[0]) for r in conn.execute('select record from orders')]
    finally:conn.close()
    if any(r['status'] in ACTIVE for r in rows):raise RuntimeError('Existing order/position is unresolved; wait for settlement and rerun')
    ex=Exchange(KalshiClientV2(Credentials.load(),dry_run=True,timeout=5))
    if any(crypto(r.get('ticker','')) and abs(number(r,'position_fp','position')) for r in ex.positions()):
        raise RuntimeError('Crypto positions exist; wait for settlement and rerun')
    if any(crypto(r.get('ticker','')) for r in ex.orders(status='resting')):
        raise RuntimeError('Crypto resting orders exist; reconcile before activation')
    cash=ex.balance()
    if cash<10:raise RuntimeError('Crypto cash below existing $10 activation minimum')
    return dict(account_access='GET_ONLY',cash=str(cash),crypto_positions=0,crypto_resting_orders=0,unresolved_records=0)

def healthy(d,started):
    z3=(d.get('lanes') or {}).get('premium_z3',{})
    z2=(d.get('lanes') or {}).get('cushion_z2',{})
    return (d.get('ts',0)>=started and d.get('mode')=='LIVE' and d.get('armed') and
        not any(d.get(k) for k in ('stopped','error','halted')) and
        d.get('contracts')==5 and d.get('max_contracts')==20 and d.get('max_risk')=='19.00' and d.get('daily_loss_budget')=='10' and
        z2.get('enabled') and z2.get('contracts')==5 and z2.get('z_min')==2 and z2.get('execution_cap')=='0.939' and
        z3.get('enabled') and z3.get('contracts')==5 and z3.get('z_min')==3 and z3.get('execution_cap')=='0.975' and
        z3.get('signal_floor')=='0.93' and z3.get('signal_ceiling')=='0.98' and
        z3.get('max_contracts')==20 and z3.get('max_risk')=='20' and z3.get('daily_loss_budget')=='25' and
        d.get('execution_checks_max')==24 and d.get('execution_retry_deadline_seconds')==25 and
        d.get('max_ioc_submissions')==3 and d.get('zero_fill_retry_enabled') is True and
        d.get('pending_reconcile_seconds')==1)

def install(staged,original):
    """Install a verified maintenance release; never reset the working ledger."""
    state=LIVE/'state'
    backup=P/('pre_upgrade_'+str(time.time_ns()));backup.mkdir()
    existing={name:(LIVE/name).exists() for name in FILES}
    for name in FILES:
        if existing[name]:shutil.copy2(LIVE/name,backup/name)
    subprocess.run(['systemctl','--user','stop',SERVICE],check=True)
    lock=(state/'process.lock').open('a');locked=False;installed=False
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
        verify_hashes(LIVE,original);live_readiness(require_heartbeat=False)
        # Consistent audit snapshot only; it is never restored over the working ledger.
        with closing(sqlite3.connect((state/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as src:
            with closing(sqlite3.connect(backup/'orders_snapshot.sqlite3')) as dst:src.backup(dst)
        installed=True
        for name in FILES:
            tmp=LIVE/(name+'.execution-upgrade');shutil.copy2(P/name,tmp);tmp.replace(LIVE/name)
        verify_hashes(LIVE,staged)
        # Import installed modules without releasing the single-writer lock or starting a runner.
        subprocess.run([sys.executable,'-c',
            'import engine,runner,market_data,lanes,execution_retry; assert lanes.LANES[lanes.Z3].contracts == 5; assert engine.CONTRACTS == 5; assert execution_retry.MAX_PRE_SUBMISSION_CHECKS == 24; assert execution_retry.MAX_IOC_SUBMISSIONS == 3'],cwd=LIVE,check=True)
        # Repeat flat/account guard under lock; existing arms are never modified.
        live_readiness(require_heartbeat=False)
    except BaseException:
        if installed:
            for name in FILES:
                if existing[name]:
                    tmp=LIVE/(name+'.execution-restore');shutil.copy2(backup/name,tmp);tmp.replace(LIVE/name)
                elif (LIVE/name).exists():(LIVE/name).unlink()
            verify_hashes(LIVE,original)
        lock.close()
        subprocess.run(['systemctl','--user','start',SERVICE],check=True)
        raise
    finally:
        if not lock.closed:lock.close()
    started=time.time()
    subprocess.run(['systemctl','--user','start',SERVICE],check=True)
    for _ in range(45):
        time.sleep(1)
        try:d=json.loads((state/'status.json').read_text())
        except (OSError,ValueError):continue
        if healthy(d,started):
            subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True)
            receipt=dict(activated=True,activated_by='authorized maintenance --execute',verified_ts=time.time(),backup=str(backup),
                source_hashes=staged,ledger_preserved=True,z2_contracts=5,z3_contracts=5,
                max_contracts=20,max_risk='19.00',daily_loss_budget='10',lanes=d['lanes'],execution_checks_max=24,execution_retry_deadline_seconds=25,
                max_ioc_submissions=3,zero_fill_retry_enabled=True)
            (P/'activation_receipt.json').write_text(json.dumps(receipt,indent=2))
            print('LIVE VERIFIED: up to3 IOC submissions within25s, only after confirmed canceled/zero fills. Both lanes size5; signal gates, price limits and shared20/$19/$10 unchanged. Full attempt history retained.')
            return
    raise RuntimeError('Installed and restarted, but healthy live status not confirmed. Inspect before retrying; no automatic rollback after restart because orders may exist.')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group()
    g.add_argument('--check',action='store_true');g.add_argument('--execute',action='store_true')
    args=p.parse_args(argv)
    original=json.loads((P/'original_hashes.json').read_text());staged=json.loads((P/'staged_hashes.json').read_text())
    verify_hashes(P,staged)
    verify_hashes(LIVE,original)
    subprocess.run([sys.executable,'-m','unittest','discover','-s',str(P),'-p','test_*.py','-q'],cwd=P,check=True)
    subprocess.run(['systemctl','--user','is-active','--quiet',SERVICE],check=True)
    check=live_readiness()
    if not args.execute:
        print(json.dumps(dict(mode='CHECK_ONLY',ready=True,activated=False,z2_contracts=5,z3_contracts=5,
            z3_signal='93–<98c',z3_z_min=3,z3_buy_cap='.975',shared_max_contracts=20,
            shared_max_risk='19.00',shared_daily_budget='10',z3_max_risk='20',z3_daily_budget='25',execution_checks_max=24,execution_retry_deadline_seconds=25,max_ioc_submissions=3,**check)))
        return
    install(staged,original)

if __name__=='__main__':main()
