"""Read-only preservation verification; report contains no account secrets."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

P=Path(__file__).resolve().parent
ROOT=P.parent
LIVE=ROOT/'kalshi_revalidation_2026_09_12/live_v1'
BASE=ROOT/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
old=json.loads((ROOT/'kalshi_noncrypto15m_2026_10_07/preservation_verification.json').read_text())
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
current={n:digest(LIVE/n) for n in old['production_source_hashes']}
assert current==old['production_source_hashes']
checkpoint=json.loads((BASE/'manifest.json').read_text())['sha256']
assert all(digest(BASE/'source'/n)==v for n,v in checkpoint.items())
status=json.loads((LIVE/'state/status.json').read_text())
service=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
with urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=10) as f:health=f.status
result=dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),production_hashes=current,
            baseline_hashes_verified=len(checkpoint),no_live_changes=True,service=service,
            dashboard_http=health,production={k:status.get(k) for k in ('ts','mode','armed','halted','error','daily_loss_budget','max_contracts','max_risk')},
            status_age_seconds=time.time()-status['ts'])
(P/'preservation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
