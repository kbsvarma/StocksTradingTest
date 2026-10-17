import sys,json,time,hashlib,subprocess,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import activate
from streamlit.testing.v1 import AppTest
LIVE=activate.LIVE
original=json.loads((P/'candidate/original_hashes.json').read_text())
activate.verify_hashes(LIVE,original)
receipt=json.loads((P/'diagnostics_receipt.json').read_text())
pid=subprocess.check_output(['systemctl','--user','show','-p','MainPID','--value',activate.SERVICE],text=True).strip()
assert pid==receipt['writer_pid']
status=json.loads((LIVE/'state/status.json').read_text())
assert status['mode']=='LIVE' and not any(status.get(k) for k in ('stopped','error','halted')) and time.time()-status['ts']<45
assert 'operations_version' not in status
obs=json.loads(Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state/status.json').read_text())
assert obs['diagnostics_version']==2 and not obs['error'] and time.time()-obs['ts']<10
# Use a fresh interpreter and the real Streamlit entry point. In-process
# panel imports with sys.path injection previously masked a production failure.
entrypoint='/home/varma/kalshi/kalshi_unbroken_bot/dashboard.py'
ui_result=P/'entrypoint_ui_verification.json'
subprocess.run([sys.executable,str(P/'check_entrypoint.py'),entrypoint,'--output',str(ui_result)],
               cwd=Path(entrypoint).parent,check=True)
views=json.loads(ui_result.read_text())['views']
checkpoint=Path('/home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28')
manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256']
assert all(hashlib.sha256((checkpoint/'source'/n).read_bytes()).hexdigest()==h for n,h in manifest.items())
result=dict(ts=time.time(),diagnostics_live=True,writer_pid=pid,writer_unchanged=True,execution_upgrade_activated=False,
    live_mode=status['mode'],live_heartbeat_age=time.time()-status['ts'],observer=obs,views=views,checkpoint_hashes_verified=len(manifest))
(P/'live_diagnostics_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
