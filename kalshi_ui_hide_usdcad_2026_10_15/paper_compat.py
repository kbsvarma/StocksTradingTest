"""Register this exact UI revision without changing frozen paper parameters."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

P=Path(__file__).resolve().parent
R=Path('/home/varma/kalshi_resting_paper_2026_10_14')
L=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(R/'observer.py')==sha(P/'original/observer.py')
assert sha(L/'dashboard_panel.py')==sha(P/'candidate/dashboard_panel.py')
sys.path.insert(0,str(R))
spec=importlib.util.spec_from_file_location('candidate_observer',P/'candidate/observer.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
guard=module.Observer.__new__(module.Observer)
guard.root=R;guard.cfg=json.loads((R/'frozen_config.json').read_text())
review=json.loads((R/'reviewed_live_source_hashes.json').read_text())
assert guard.source_guard()=='production_source_changed'
expected=dict(review['before'],**review['updates'])
assert [n for n,h in expected.items() if sha(L/n)!=h]==['dashboard_panel.py']
review['updates']['dashboard_panel.py']=sha(L/'dashboard_panel.py')
review['reviewed_ts']=time.time()
review['ui_deployment']=str(P/'deployment.json')
review['reason']='User-authorized UI-only USD/CAD archival; frozen strategy, parameters, ledger and live trading unchanged.'
with tempfile.TemporaryDirectory() as td:
    temp=Path(td);guard.root=temp
    (temp/'reviewed_live_source_hashes.json').write_text(json.dumps(review))
    assert guard.source_guard()==''
    reviewed_hash=review['updates']['dashboard_panel.py']
    review['updates']['dashboard_panel.py']='0'*64
    (temp/'reviewed_live_source_hashes.json').write_text(json.dumps(review))
    assert guard.source_guard()=='production_source_changed'
    review['updates']['dashboard_panel.py']=reviewed_hash
    review['updates']['lanes.py']=sha(L/'lanes.py')
    (temp/'reviewed_live_source_hashes.json').write_text(json.dumps(review))
    assert guard.source_guard()=='invalid_reviewed_source_manifest'
    del review['updates']['lanes.py']
    # All existing paper tests execute against the candidate without network access.
    import unittest
    with patch.dict(sys.modules,{'observer':module}):
        suite=unittest.defaultTestLoader.discover(str(R),pattern='test_*.py')
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        assert result.wasSuccessful()

status=json.loads((R/'status.json').read_text())
assert status['running_jobs']==0
backup=R/'repairs/ui_usdcad_20261015T1822Z'
backup.mkdir(parents=True,exist_ok=False)
for name in ('observer.py','reviewed_live_source_hashes.json','status.json'):
    shutil.copy2(R/name,backup/name)
protected={p.name:sha(p) for p in R.iterdir() if p.is_file() and p.name in ('frozen_config.json','paper.py')}
before_service=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-tennis90-paper.service','-p','MainPID'],text=True)
subprocess.run(['systemctl','--user','stop','kalshi-resting-paper.service'],check=True)
try:
    for name,data in [('observer.py',(P/'candidate/observer.py').read_bytes()),('reviewed_live_source_hashes.json',(json.dumps(review,indent=2)+'\n').encode())]:
        target=R/(name+'.new');target.write_bytes(data);target.replace(R/name)
finally:
    subprocess.run(['systemctl','--user','start','kalshi-resting-paper.service'],check=True)
with (R/'CHANGELOG.txt').open('a') as f:
    f.write('\n2026-10-15 14:22 ET — Authorized USD/CAD dashboard archival compatibility\n'
            'UI-only dashboard_panel.py revision accepted by exact reviewed hash. The\n'
            'source guard permits that one named additional file; all unknown revisions\n'
            'remain blocked. Existing paper tests plus positive/negative guard checks pass.\n'
            'Paper observer restarted with zero jobs; original receipts, ledger, cutoff,\n'
            'frozen configuration and strategy preserved. Backup: repairs/ui_usdcad_20261015T1822Z.\n'
            'Brief collection pause while integrity guard awaited the authorized UI hash.\n'
            'Live trader and tennis service unchanged. Receipt: '+str(P/'deployment.json')+'\n')
assert all(sha(R/n)==h for n,h in protected.items())
assert subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-tennis90-paper.service','-p','MainPID'],text=True)==before_service
(P/'paper_compat.json').write_text(json.dumps(dict(ts=time.time(),tests=result.testsRun,guard_checks=3,backup=str(backup),frozen_unchanged=True,trader_and_tennis_unchanged=True),indent=2)+'\n')
print((P/'paper_compat.json').read_text())
