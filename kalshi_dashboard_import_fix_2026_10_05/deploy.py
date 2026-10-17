"""Replace only the read-only Streamlit entry point and restart its service."""
import hashlib,json,shutil,subprocess,time
from pathlib import Path
P=Path(__file__).resolve().parent
live=Path('/home/varma/kalshi/kalshi_unbroken_bot/dashboard.py')
trader='kalshi-crypto-z2-live.service'
def run(args):return subprocess.check_output(args,text=True).strip()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
pid=run(['systemctl','--user','show',trader,'-p','MainPID','--value'])
assert pid!='0'
assert live.read_bytes()==(P/'dashboard_before.py').read_bytes(),'Entry point changed since inspection'
backup=P/('dashboard_backup_'+str(time.time_ns())+'.py');shutil.copy2(live,backup)
started=time.time()
try:
    temp=live.with_suffix('.import-fix');shutil.copy2(P/'dashboard.py',temp);temp.replace(live)
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True)
    subprocess.run(['/home/varma/kalshi/venv/bin/python',str(P/'check_entrypoint.py'),str(live),'--output',str(P/'production_verified.json')],cwd=live.parent,check=True)
except BaseException:
    temp=live.with_suffix('.restore');shutil.copy2(backup,temp);temp.replace(live)
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True)
    raise
assert run(['systemctl','--user','show',trader,'-p','MainPID','--value'])==pid
result=dict(started_ts=started,verified_ts=time.time(),changed_file=str(live),sha256=digest(live),backup=str(backup),
            trading_pid=pid,trading_process_unchanged=True,execution_upgrade_activated=False)
(P/'deployment_receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
