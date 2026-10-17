"""User-run deployment and activation. Never submits a test order."""
import argparse,fcntl,hashlib,json,shutil,sqlite3,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LIVE=Path('/home/varmak/kalshi_revalidation_2026_09_12/live_v1')
PYTHON='/home/varmak/kalshi/venv/bin/python'
SERVICE='kalshi-crypto-z2-live.service'
FILES=['sizing.py','engine.py','market_data.py','runner.py','dashboard_panel.py','test_engine.py','test_signals.py','test_scaling.py']
def run(*args,**kw):return subprocess.run(args,check=True,**kw)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not args.execute:raise SystemExit('Use --execute to install and activate 2 contracts, 8 total, $7.60 exposure, $10 daily budget.')
    lock=(ROOT/'activation.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (ROOT/'activated.json').exists():raise SystemExit('This release was already activated; no changes made.')
    expected=json.loads((ROOT/'manifest.json').read_text())
    for name,sha in expected.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha:raise SystemExit('Release changed: '+name)
    for src in (ROOT/'base').glob('*.py'):
        if (LIVE/src.name).read_bytes()!=src.read_bytes():raise SystemExit('Live source changed; review before deployment: '+src.name)
    run(PYTHON,'-m','unittest','discover','-q',cwd=ROOT/'release')
    status=json.loads((LIVE/'state/status.json').read_text())
    if time.time()-status['ts']>45 or status.get('active') or status.get('halted') or status.get('error'):
        raise SystemExit('Live state is stale, blocked, or has active orders. No changes made; retry once clear.')
    run('systemctl','--user','stop',SERVICE)
    backup=ROOT/('backup-'+str(time.time_ns()));backup.mkdir()
    installed=False;started=False
    try:
        # Snapshot after the running process has released the ledger.
        conn=sqlite3.connect(LIVE/'state/orders.sqlite3');dest=sqlite3.connect(backup/'orders.sqlite3')
        conn.backup(dest);dest.close();conn.close()
        for name in FILES:
            if (LIVE/name).exists():shutil.copy2(LIVE/name,backup/name)
        # Fail closed if a last-minute order appeared while stopping.
        status=json.loads((LIVE/'state/status.json').read_text())
        if status.get('active'):raise RuntimeError('An order appeared during shutdown; retry after settlement.')
        installed=True
        for name in FILES:shutil.copy2(ROOT/'release'/name,LIVE/name)
        run(PYTHON,'runner.py','--preflight',cwd=LIVE)
        (LIVE/'state/ARM_2CT').write_text('2\n')
        run('systemctl','--user','start',SERVICE);started=True
        for _ in range(20):
            time.sleep(1)
            s=json.loads((LIVE/'state/status.json').read_text())
            if s.get('contracts')==2 and s.get('mode')=='LIVE' and not s.get('stopped') and time.time()-s['ts']<10:
                if s.get('halted') or s.get('error'):raise RuntimeError('Started but entry blocked: '+str(s.get('halted') or s.get('error')))
                receipt={'ts':time.time(),'backup':str(backup),'contracts':2,'max_contracts':8,'max_risk':'7.60','daily_loss_budget':'10'}
                (ROOT/'activated.json').write_text(json.dumps(receipt,indent=2))
                print('CONFIRMED LIVE: 2 contracts per market; max 8 contracts; $7.60 exposure; $10 daily loss budget. Dashboard updates automatically.')
                return
        raise RuntimeError('Two-contract heartbeat not confirmed; inspect service before taking further action.')
    except Exception:
        if not started:
            if installed:
                for name in FILES:
                    if (backup/name).exists():shutil.copy2(backup/name,LIVE/name)
                    else:(LIVE/name).unlink(missing_ok=True)
            (LIVE/'state/ARM_2CT').unlink(missing_ok=True)
            run('systemctl','--user','start',SERVICE)
            print('Activation did not complete. Original code restored and original service restarted.')
        else:
            print('New process started; no automatic rollback because it may have placed two-contract orders. Inspect status and preserve the ledger.')
        raise
if __name__=='__main__':main()
