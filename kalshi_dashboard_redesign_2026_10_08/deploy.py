"""Deploy only dashboard files, retaining backups and checking trader preservation."""
import hashlib,json,shutil,subprocess,time
from pathlib import Path
import requests

P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
WRAPPER=Path('/home/varma/kalshi/kalshi_unbroken_bot/dashboard.py')
THEME=WRAPPER.parent/'.streamlit/config.toml'
PY='/home/varma/kalshi/venv/bin/python'
def run(args):return subprocess.check_output(args,text=True).strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
assert json.loads((P/'preview_validation.json').read_text())['passed']
assert WRAPPER.read_bytes()==(P/'original/dashboard.py').read_bytes(),'Wrapper changed since inspection'
assert (LIVE/'dashboard_panel.py').read_bytes()==(P/'original/dashboard_panel.py').read_bytes(),'Panel changed since inspection'
assert not THEME.exists(),'Unexpected existing theme configuration'
assert not (LIVE/'dashboard.css').exists(),'Unexpected existing stylesheet'
trader='kalshi-crypto-z2-live.service'
pid=run(['systemctl','--user','show',trader,'-p','MainPID','--value']);assert pid!='0'
protected={str(p):digest(p) for p in LIVE.glob('*.py') if p.name!='dashboard_panel.py'}
baseline=Path('/home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28')
for n,h in json.loads((baseline/'manifest.json').read_text())['sha256'].items():
    assert digest(baseline/'source'/n)==h
backup=P/('backup_'+str(time.time_ns()));backup.mkdir()
targets={'dashboard.css':LIVE/'dashboard.css','dashboard_panel.py':LIVE/'dashboard_panel.py','dashboard.py':WRAPPER,'config.toml':THEME}
prior={}
for name,path in targets.items():
    prior[name]=path.exists()
    if prior[name]:shutil.copy2(path,backup/name)
(backup/'existing.json').write_text(json.dumps(prior,indent=2))
def replace(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    temp=dst.with_name(dst.name+'.dashboard-update');shutil.copy2(src,temp);temp.replace(dst)
started=time.time()
try:
    for name,path in targets.items():replace(P/'candidate'/name,path)
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True)
    subprocess.run([PY,str(P/'validate.py'),str(WRAPPER),str(P/'production_validation.json')],cwd=WRAPPER.parent,check=True)
    for _ in range(10):
        try:
            if requests.get('http://192.168.3.136:8507/_stcore/health',timeout=2).status_code==200:break
        except requests.RequestException:pass
        time.sleep(1)
    else:raise RuntimeError('Dashboard health failed')
except BaseException:
    for name,path in targets.items():
        if prior[name]:replace(backup/name,path)
        elif path.exists():path.unlink()
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True)
    raise
assert run(['systemctl','--user','show',trader,'-p','MainPID','--value'])==pid,'Trader PID changed'
assert all(digest(Path(p))==h for p,h in protected.items()),'Non-dashboard source changed'
status=json.loads((LIVE/'state/status.json').read_text())
assert time.time()-status['ts']<45
result=dict(deployed=True,started_ts=started,verified_ts=time.time(),backup=str(backup),trading_pid=pid,
            trader_unchanged=True,protected_source_count=len(protected),files={str(path):digest(path) for path in targets.values()},
            dashboard_pid=run(['systemctl','--user','show','kalshi-dashboard.service','-p','MainPID','--value']),
            live_status={k:status.get(k) for k in ('mode','armed','halted','error','contracts','max_contracts','max_risk','daily_loss_budget')})
(P/'deployment_receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
