"""Install only read-only diagnostics; never stop/restart/arm the trading service."""
import sys,json,hashlib,shutil,subprocess,time,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import activate
LIVE=activate.LIVE
FILES=('price_rules.py','io_budget.py','operations_view.py','observe_execution.py','dashboard_panel.py')
def run(*args):return subprocess.check_output(args,text=True).strip()
def pid():return run('systemctl','--user','show','-p','MainPID','--value',activate.SERVICE)
original=json.loads((P/'candidate/original_hashes.json').read_text())
staged=json.loads((P/'candidate/staged_hashes.json').read_text())
activate.verify_hashes(LIVE,original);activate.verify_hashes(P/'candidate',staged)
backup=P/('diagnostics_backup_'+str(time.time_ns()));backup.mkdir()
old_pid=pid();assert old_pid!='0'
existing={n:(LIVE/n).exists() for n in FILES}
for n in FILES:
    if existing[n]:shutil.copy2(LIVE/n,backup/n)
override=Path.home()/'.config/systemd/user/kalshi-execution-observer.service.d/50-operations.conf'
unit=override.read_bytes() if override.exists() else None
try:
    for n in FILES:
        tmp=LIVE/(n+'.diagnostics-upgrade');shutil.copy2(P/'candidate'/n,tmp);tmp.replace(LIVE/n)
    activate.verify_hashes(LIVE,{n:staged[n] for n in FILES})
    run('systemctl','--user','restart','kalshi-dashboard.service')
    ok=False
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=2) as r:ok=r.read()==b'ok'
        except Exception:pass
        if ok:break
        time.sleep(1)
    assert ok,'Dashboard health failed'
    activate.install_observer(backup)
    assert pid()==old_pid,'Writer process changed during diagnostics deployment'
except BaseException:
    for n in FILES:
        if existing[n]:shutil.copy2(backup/n,LIVE/n)
        else:(LIVE/n).unlink(missing_ok=True)
    if unit is None:override.unlink(missing_ok=True)
    else:override.write_bytes(unit)
    run('systemctl','--user','daemon-reload')
    run('systemctl','--user','restart','kalshi-dashboard.service','kalshi-execution-observer.service')
    raise
(P/'initial_live_hashes.json').write_text(json.dumps(original,indent=2))
original.update({n:staged[n] for n in FILES})
(P/'candidate/original_hashes.json').write_text(json.dumps(original,indent=2))
receipt=dict(ts=time.time(),diagnostics_live=True,execution_upgrade_activated=False,backup=str(backup),
    writer_pid=old_pid,writer_unchanged=True,source_hashes={n:staged[n] for n in FILES})
(P/'diagnostics_receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
