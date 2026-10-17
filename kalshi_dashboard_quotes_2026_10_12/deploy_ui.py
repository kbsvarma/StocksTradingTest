"""Install only three display files. No bot restart, state writes or private API."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
STATE=LIVE/'state'
CHANGED=('dashboard_quotes.py','dashboard.css','dashboard_panel.py')
SERVICES=('kalshi-crypto-z2-live.service','kalshi-dashboard.service',
          'kalshi-execution-observer.service','kalshi-tennis90-paper.service')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def services():
    return {n:subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True) for n in SERVICES}
def protected():
    paths=[p for p in LIVE.iterdir() if p.suffix=='.py' and p.name not in CHANGED]
    paths += [p for p in STATE.iterdir() if p.is_file() and p.name.startswith(('HALT','ARM'))]
    checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
    manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256']
    assert all(sha(checkpoint/'source'/n)==v for n,v in manifest.items())
    paths += list((checkpoint/'source').iterdir())
    paths += [P.parent/'kalshi/kalshi_unbroken_bot/dashboard.py']
    return {str(p):sha(p) for p in paths if p.is_file()}
def replace(src,dest):
    temp=dest.with_name(dest.name+'.ui-quotes-tmp')
    shutil.copy2(src,temp)
    with temp.open('rb') as f:os.fsync(f.fileno())
    os.replace(temp,dest)

assert not (P/'deployment_receipt.json').exists(),'Already deployed'
assert json.loads((P/'ui_preview.json').read_text())['passed']
assert (P/'tests.log').read_text().rstrip().endswith('OK')
manifest=json.loads((P/'candidate_manifest.json').read_text())
assert all(sha(P/'candidate'/n)==h for n,h in manifest.items())
assert all(sha(LIVE/n)==sha(P/'original'/n) for n in ('dashboard_panel.py','dashboard.css'))
assert not (LIVE/'dashboard_quotes.py').exists(),'Unexpected existing display feed'
before=protected();units=services();status=json.loads((STATE/'status.json').read_text())
assert time.time()-status['ts']<30 and status['armed'] and status['mode']=='LIVE'
save(P/'preservation_before.json',dict(ts=time.time(),hashes=before,services=units))
backup=P/'deployment_backup';backup.mkdir()
for n in CHANGED:
    if (LIVE/n).exists():shutil.copy2(LIVE/n,backup/n)
# Dependency first, panel last. The existing fragment reloads panel/CSS every 10s.
for n in CHANGED:replace(P/'candidate'/n,LIVE/n)
assert protected()==before,'Protected source or flags changed'
assert services()==units,'Unexpected service change'
assert all(sha(LIVE/n)==manifest[n] for n in CHANGED)
save(P/'deployment_receipt.json',dict(deployed=True,ts=time.time(),changed=list(CHANGED),
                                    source_hashes={n:sha(LIVE/n) for n in CHANGED},
                                    protected_hashes=before,services=units,backup=str(backup),
                                    restart=False,private_api=False))
print('UI deployed: public quotes and next-window countdown. All bot source, flags, checkpoint and service PIDs unchanged.')
