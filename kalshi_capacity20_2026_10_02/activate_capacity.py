"""User-run capacity increase to max20/$19; daily budget stays$10; default/check mode is read-only.

Only --execute may stop/restart the live service or replace its source files.
Never copies ledgers, credentials, HALT files, or activation/arming files.
"""
import argparse
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
FILES=('engine.py','sizing.py')
ACTIVE=('intent','unknown','ack','open','accounting_pending')


def verify_hashes(root,expected):
    for name,wanted in expected.items():
        if name not in ('engine.py','market_data.py','runner.py','dashboard_panel.py',
                        'sizing.py','execution_retry.py','kalshi_client.py'):
            raise RuntimeError('Unexpected source file in manifest')
        path=root/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:
            raise RuntimeError(f'{root.name}/{name} changed; refresh staging before activation.')


def live_readiness(require_heartbeat=True):
    # Client dry_run enforces GET-only even if a future call accidentally changes.
    from kalshi_client import KalshiClientV2,Credentials
    from engine import Exchange,crypto,number
    state=LIVE/'state'
    if (state/'HALT').exists():raise RuntimeError('Manual HALT exists; preserved.')
    if (state/'ARM_5CT').read_text().strip()!='5':raise RuntimeError('Existing five-contract arm is not valid.')
    if require_heartbeat:
        d=json.loads((state/'status.json').read_text())
        if time.time()-d['ts']>45 or d.get('mode')!='LIVE' or not d.get('armed') or d.get('stopped') or d.get('error') or d.get('halted'):
            raise RuntimeError('Current live heartbeat is stale or blocked; inspect before upgrading.')
    conn=sqlite3.connect((state/'orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)
    try:rows=[json.loads(r[0]) for r in conn.execute('select record from orders')]
    finally:conn.close()
    if any(r['status'] in ACTIVE for r in rows):
        raise RuntimeError('Existing position/order is unresolved; wait for normal settlement and reconciliation, then rerun.')
    ex=Exchange(KalshiClientV2(Credentials.load(),dry_run=True,timeout=5))
    if any(crypto(r.get('ticker','')) and abs(number(r,'position_fp','position')) for r in ex.positions()):
        raise RuntimeError('Crypto positions still exist; wait for settlement.')
    if any(crypto(r.get('ticker','')) for r in ex.orders(status='resting')):
        raise RuntimeError('Crypto resting orders exist; reconcile before activation.')
    balance=ex.balance()
    if balance<10:raise RuntimeError('Less than $10 crypto free cash for existing preflight requirement.')
    return dict(clear=True,unresolved_ledger_records=0,crypto_positions=0,crypto_resting_orders=0,
                account_access='GET_ONLY',free_cash=str(balance))


def healthy_new_status(d,started):
    return (d.get('ts',0)>=started and d.get('mode')=='LIVE' and d.get('armed') and
            not d.get('stopped') and not d.get('error') and not d.get('halted') and
            d.get('signal_ask_ceiling_dollars')=='0.95' and d.get('execution_cap_dollars')=='0.939' and
            d.get('contracts')==5 and d.get('max_contracts')==20 and
            d.get('max_risk')=='19.00' and d.get('daily_loss_budget')=='10')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true',help='read-only checks; no activation (default)')
    modes.add_argument('--execute',action='store_true',help='USER ONLY: activate the reviewed trading change')
    args=parser.parse_args()
    original=json.loads((P/'original_hashes.json').read_text())
    staged=json.loads((P/'staged_hashes.json').read_text())
    verify_hashes(P,staged)
    verify_hashes(LIVE,original)
    subprocess.run([sys.executable,'-m','unittest','test_engine','test_signals','test_retry','test_activation','test_portfolio','-q'],cwd=P,check=True)
    subprocess.run(['systemctl','--user','is-active','--quiet',SERVICE],check=True)
    check=live_readiness()
    if not args.execute:
        print(json.dumps(dict(mode='CHECK_ONLY',ready=True,activated=False,
            signal_ceiling='0.95 exclusive',execution_cap='0.939 inclusive',contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='10',**check)))
        return

    # No mutation above this line. This branch must be invoked by the user.
    backup=P/('pre_upgrade_'+str(time.time_ns()));backup.mkdir()
    for name in FILES:shutil.copy2(LIVE/name,backup/name)
    subprocess.run(['systemctl','--user','stop',SERVICE],check=True)
    try:
        verify_hashes(LIVE,original)
        live_readiness(require_heartbeat=False)
        for name in FILES:
            temp=LIVE/(name+'.capacity20-upgrade')
            shutil.copy2(P/name,temp);temp.replace(LIVE/name)
        verify_hashes(LIVE,staged)
        subprocess.run([sys.executable,'runner.py','--preflight'],cwd=LIVE,check=True)
    except BaseException:
        for name in FILES:
            temp=LIVE/(name+'.capacity20-restore')
            shutil.copy2(backup/name,temp);temp.replace(LIVE/name)
        verify_hashes(LIVE,original)
        subprocess.run(['systemctl','--user','start',SERVICE],check=True)
        raise
    started=time.time()
    subprocess.run(['systemctl','--user','start',SERVICE],check=True)
    for _ in range(35):
        time.sleep(1)
        try:d=json.loads((LIVE/'state/status.json').read_text())
        except (OSError,ValueError):continue
        if healthy_new_status(d,started):
            receipt=dict(activated=True,activated_by='user --execute',verified_ts=time.time(),
                signal_ceiling='0.95 exclusive',execution_cap='0.939 inclusive',contracts=5,
                max_contracts=20,max_risk='19.00',daily_loss_budget='10',backup=str(backup),
                source_hashes=staged,state_preserved=True)
            (P/'activation_receipt.json').write_text(json.dumps(receipt,indent=2))
            print('LIVE VERIFIED: 5 contracts per market; max20 contracts/$19 exposure/$10 daily loss budget. Signal95, execution93.9c, z2 and bounded retries unchanged.')
            return
    raise RuntimeError('Source installed, but healthy live heartbeat was not confirmed. Inspect service/status; no automatic rollback after restart because new orders may exist.')


if __name__=='__main__':main()
