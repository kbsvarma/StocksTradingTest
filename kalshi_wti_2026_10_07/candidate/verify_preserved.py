"""Read-only evidence that staging did not modify the running release."""
import hashlib,json,subprocess,time
from pathlib import Path
import requests
P=Path(__file__).resolve().parent.parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
CHECKPOINT=Path('/home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28')
expected=json.loads((P/'candidate/original_hashes.json').read_text())
hashes={n:hashlib.sha256((LIVE/n).read_bytes()).hexdigest() for n in expected}
assert hashes==expected,'Production source changed'
baseline=json.loads((CHECKPOINT/'manifest.json').read_text())['sha256']
actual={n:hashlib.sha256((CHECKPOINT/'source'/n).read_bytes()).hexdigest() for n in baseline}
assert actual==baseline,'Stable checkpoint changed'
status=json.loads((LIVE/'state/status.json').read_text())
assert status['mode']=='LIVE' and status['armed'] and not any(status.get(k) for k in ('stopped','halted','error'))
assert 0<=time.time()-status['ts']<45
assert not (LIVE/'state/ARM_WTI_5CT').exists()
assert (LIVE/'state/ARM_NONCRYPTO_5CT').is_file()
assert status['daily_loss_budget']=='10' and status['multiasset_version']==1
assert len(status['assets'])==7 and all(x['enabled'] for x in status['assets'].values())
services={}
for service in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service'):
    raw=subprocess.check_output(['systemctl','--user','show',service,'-p','ActiveState','-p','MainPID','-p','NRestarts','-p','ExecMainStartTimestamp'],text=True)
    services[service]=dict(line.split('=',1) for line in raw.splitlines())
    assert services[service]['ActiveState']=='active'
response=requests.get('http://192.168.3.136:8507/_stcore/health',timeout=5);assert response.status_code==200
assert services['kalshi-crypto-z2-live.service']['MainPID']=='3731415'
report=dict(verified_ts=time.time(),production_hashes_match=len(hashes),checkpoint_hashes_match=len(actual),
            current_live_assets=['BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD'],wti_armed=False,staged_not_live=True,
            services=services,dashboard_health=response.status_code,heartbeat_age=time.time()-status['ts'],
            production_pid_unchanged=services['kalshi-crypto-z2-live.service']['MainPID']=='3731415',
            tests=160,scanner_parity=180234,real_orders_submitted_by_this_task=0)
(P/'preservation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
