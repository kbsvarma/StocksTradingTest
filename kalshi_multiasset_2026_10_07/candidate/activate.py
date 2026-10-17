"""Read-only verification by default. User-run --execute installs and arms this release."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from lanes import NEW_ASSETS, ALL_ASSETS, NONCRYPTO_ARM, NONCRYPTO_VERSION, route_for, lane_for, Z2, Z3

P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
SERVICE='kalshi-crypto-z2-live.service'
FILES=('engine.py','runner.py','market_data.py','lanes.py','pyth_data.py','dashboard_panel.py',
       'operations_view.py','observe_execution.py','execution_retry.py','price_rules.py','io_budget.py','kalshi_client.py','sizing.py')
ACTIVE=('intent','unknown','ack','open','accounting_pending')

def verify_hashes(root,expected):
    for name,wanted in expected.items():
        if name not in FILES:raise RuntimeError('Unexpected manifest path')
        path=root/name
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:
            raise RuntimeError(str(path)+' changed; refresh staging before activation')

def verify_bundle():
    for name,digest in json.loads((P/'bundle_hashes.json').read_text()).items():
        path=P/name
        if Path(name).name!=name or not name.endswith('.py') or path.is_symlink():raise RuntimeError('Unsafe bundle path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Staged verification bundle changed: '+name)

def live_readiness(require_heartbeat=True):
    from kalshi_client import KalshiClientV2,Credentials
    from engine import Exchange,crypto,number
    from runner import legacy_safe
    state=LIVE/'state'
    for name in ('HALT','HALT_Z3',*('HALT_'+a for a in NEW_ASSETS)):
        if (state/name).exists():raise RuntimeError(name+' exists; preserved')
    for name in ('ARM_5CT','ARM_Z3_5CT'):
        if (state/name).read_text().strip()!='5':raise RuntimeError('Existing arm invalid: '+name)
    if (state/'ARM_Z3_1CT').exists():raise RuntimeError('Older one-contract z3 arm exists')
    reason=legacy_safe()
    if reason:raise RuntimeError(reason)
    if require_heartbeat:
        d=json.loads((state/'status.json').read_text())
        if not 0<=time.time()-d.get('ts',0)<=45 or d.get('mode')!='LIVE' or not d.get('armed') or any(d.get(k) for k in ('stopped','error','halted')):
            raise RuntimeError('Existing bot heartbeat is stale or blocked')
    with closing(sqlite3.connect((state/'orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)) as conn:
        rows=[json.loads(r[0]) for r in conn.execute('select record from orders')]
    if any(r['status'] in ACTIVE for r in rows):raise RuntimeError('Existing order/position unresolved; wait for settlement and rerun')
    ex=Exchange(KalshiClientV2(Credentials.load(),dry_run=True,timeout=5))
    if any(crypto(r.get('ticker','')) and abs(number(r,'position_fp','position')) for r in ex.positions()):
        raise RuntimeError('Supported-market positions exist; wait for settlement and rerun')
    if any(crypto(r.get('ticker','')) for r in ex.orders(status='resting')):
        raise RuntimeError('Supported-market resting orders exist; reconcile before activation')
    balances={str(i):str(ex.balance(i)) for i in (0,2)}
    from decimal import Decimal
    if any(Decimal(c)<10 for c in balances.values()):raise RuntimeError('Each enabled route needs at least $10 free cash at activation')
    return dict(account_access='GET_ONLY',balances=balances,positions=0,resting_orders=0,unresolved_records=0,ledger_records=len(rows))

def source_readiness():
    import market_data as m
    errors=m.warm_sources()
    if errors:raise RuntimeError('Pyth warmup incomplete: '+str(errors))
    info={};T=int(time.time()//60)*60
    for asset in NEW_ASSETS:
        markets=m.get(m.BASE+'/markets?series_ticker=KX'+asset+'15M&status=open&limit=10')['markets']
        market=next((x for x in markets if m.timestamp(x['open_time'])<=T<m.timestamp(x['close_time'])),None)
        if not market:raise RuntimeError('No current market for '+asset)
        if market.get('exchange_index')!=route_for(dict(asset=asset)) or float(market.get('notional_value_dollars',0))!=1 or m.timestamp(market['close_time'])-m.timestamp(market['open_time'])!=900:
            raise RuntimeError('Unexpected market metadata: '+asset)
        sampled=m.PYTH.sample(asset,market,T,m.get,m.BASE)
        if sampled is None:raise RuntimeError('Latest completed minute history incomplete: '+asset+'; retry next minute')
        info[asset]=dict(ticker=market['ticker'],exchange_index=market['exchange_index'],bars=sampled[2]['bars_count'],latest_completed_second=sampled[2]['latest_completed_second'])
    return info

def healthy(d,started):
    if not (d.get('ts',0)>=started and d.get('mode')=='LIVE' and d.get('armed') and d.get('multiasset_version')==1 and
        not any(d.get(k) for k in ('stopped','error','halted')) and
        d.get('contracts')==5 and d.get('max_contracts')==20 and d.get('max_risk')=='19.00' and d.get('daily_loss_budget')=='10' and
        d.get('execution_checks_max')==24 and d.get('execution_retry_deadline_seconds')==25 and d.get('max_ioc_submissions')==3 and
        d.get('zero_fill_retry_enabled') is True and d.get('pending_reconcile_seconds')==1):return False
    for asset in ALL_ASSETS:
        a=d.get('assets',{}).get(asset,{})
        if not a.get('enabled') or a.get('contracts')!=5 or a.get('exchange_index')!=route_for(dict(asset=asset)):return False
        for lane in (Z2,Z3):
            r=lane_for(dict(asset=asset,lane=lane));got=a.get('rules',{}).get(lane,{})
            expected=dict(z_base_at_5m=r.z,z_slope=r.z_slope,min_minutes=r.min_minutes,max_minutes=r.max_minutes,
                          signal_floor=str(r.floor),signal_ceiling=str(r.ceiling),execution_cap=str(r.cap))
            if got!=expected:return False
    premium=d.get('lanes',{}).get(Z3,{})
    return bool(premium.get('enabled') and premium.get('max_contracts')==20 and premium.get('max_risk')=='20' and premium.get('daily_loss_budget')=='25')

def restore_files(backup,existing,arm_before):
    # Only called BEFORE restarting the upgraded runner. Never restore a ledger.
    for name in FILES:
        if existing[name]:
            tmp=LIVE/(name+'.multiasset-restore');shutil.copy2(backup/name,tmp);tmp.replace(LIVE/name)
        elif (LIVE/name).exists():(LIVE/name).unlink()
    arm=LIVE/'state'/NONCRYPTO_ARM
    if arm_before is None:arm.unlink(missing_ok=True)
    else:arm.write_bytes(arm_before)

def install(staged,original):
    state=LIVE/'state';backup=P/('pre_upgrade_'+str(time.time_ns()));backup.mkdir()
    existing={name:(LIVE/name).exists() for name in FILES}
    arm=state/NONCRYPTO_ARM;arm_before=arm.read_bytes() if arm.exists() else None
    subprocess.run(['systemctl','--user','stop',SERVICE],check=True)
    lock=(state/'process.lock').open('a');installed=False
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        verify_hashes(LIVE,original);verify_hashes(P,staged);live_readiness(require_heartbeat=False)
        for name in FILES:
            if existing[name]:shutil.copy2(LIVE/name,backup/name)
        (backup/'arm_before.json').write_text(json.dumps(dict(arm=arm_before.decode() if arm_before is not None else None)))
        with closing(sqlite3.connect((state/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as src:
            with closing(sqlite3.connect(backup/'orders_snapshot.sqlite3')) as dst:src.backup(dst)
        installed=True
        for name in FILES:
            tmp=LIVE/(name+'.multiasset-upgrade');shutil.copy2(P/name,tmp);tmp.replace(LIVE/name)
        verify_hashes(LIVE,staged)
        subprocess.run([sys.executable,'-c','import engine,runner,market_data,lanes,pyth_data,operations_view; assert len(market_data.ASSETS)==7; assert engine.CONTRACTS==5'],cwd=LIVE,check=True)
        live_readiness(require_heartbeat=False)
        tmp=arm.with_suffix('.tmp');tmp.write_text(json.dumps(dict(version=NONCRYPTO_VERSION,contracts=5,assets=list(NEW_ASSETS)))+'\n');tmp.replace(arm)
    except BaseException:
        if installed:
            restore_files(backup,existing,arm_before);verify_hashes(LIVE,original)
        lock.close();subprocess.run(['systemctl','--user','start',SERVICE],check=True)
        raise
    finally:
        if not lock.closed:lock.close()
    started=time.time();subprocess.run(['systemctl','--user','start',SERVICE],check=True)
    # Once restarted, never auto-restore old code or ledger: orders may exist.
    for _ in range(60):
        time.sleep(1)
        try:d=json.loads((state/'status.json').read_text())
        except (OSError,ValueError):continue
        if healthy(d,started):
            subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service','kalshi-execution-observer.service'],check=True)
            receipt=dict(activated=True,verified_ts=time.time(),backup=str(backup),source_hashes=staged,ledger_preserved=True,
                         assets=d['assets'],contracts=5,shared_max_contracts=20,shared_max_risk='19.00',shared_daily_budget='10')
            (P/'activation_receipt.json').write_text(json.dumps(receipt,indent=2))
            print('LIVE VERIFIED: crypto + Copper + Natural gas + EUR/USD; 5 contracts per entry; shared20/$19/$10. Dashboard has separate market views.')
            return
    raise RuntimeError('Installed/restarted but healthy status unconfirmed. Inspect before retrying; no automatic rollback after possible orders.')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group()
    g.add_argument('--check',action='store_true');g.add_argument('--execute',action='store_true');args=p.parse_args(argv)
    original=json.loads((P/'original_hashes.json').read_text());staged=json.loads((P/'staged_hashes.json').read_text())
    if set(staged)!=set(FILES) or set(original)!=set(FILES)-{'pyth_data.py'}:raise RuntimeError('Incomplete source manifest')
    verify_bundle();verify_hashes(P,staged);verify_hashes(LIVE,original)
    subprocess.run([sys.executable,'-m','unittest','discover','-s',str(P),'-p','test_*.py','-q'],cwd=P,check=True)
    subprocess.run(['systemctl','--user','is-active','--quiet',SERVICE],check=True)
    sources=source_readiness();check=live_readiness()
    if not args.execute:
        print(json.dumps(dict(mode='CHECK_ONLY',ready=True,activated=False,contracts=5,assets=list(NEW_ASSETS),
                             shared_max_contracts=20,shared_max_risk='19.00',shared_daily_budget='10',sources=sources,**check)))
        return
    install(staged,original)

if __name__=='__main__':main()
