"""Read-only preservation check. No account client or service writes."""
from pathlib import Path
import hashlib,json,subprocess,time
P=Path(__file__).resolve().parent;R=P.parent
checkpoint=R/'kalshi_stable_baseline_2026_09_28'
expected=json.loads((checkpoint/'manifest.json').read_text())['sha256']
stable={name:hashlib.sha256((checkpoint/'source'/name).read_bytes()).hexdigest()==sha for name,sha in expected.items()}
assert all(stable.values()),stable
remote="""import pathlib,json,hashlib,time,subprocess
p=pathlib.Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1');release=pathlib.Path('/home/varma/kalshi_execution_improvements_2026_10_03/candidate');expected=json.loads((release/'staged_hashes.json').read_text());s=json.loads((p/'state/status.json').read_text());matches={k:hashlib.sha256((p/k).read_bytes()).hexdigest()==v for k,v in expected.items()}
print(json.dumps(dict(ts=time.time(),source_matches=matches,mode=s.get('mode'),heartbeat_age_seconds=time.time()-s['ts'],stopped=s.get('stopped'),error=s.get('error'),halt=s.get('halt_reason'),services=subprocess.check_output(['systemctl','--user','is-active','kalshi-crypto-z2-live.service','kalshi-dashboard.service'],text=True).splitlines())))
"""
cmd=['ssh','-i','/Users/varmakammili/.ssh/id_ed25519_migration_192_168_3_136','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=10','varma@192.168.3.136','/home/varma/kalshi/venv/bin/python','-']
out=json.loads(subprocess.check_output(cmd,input=remote,text=True));assert all(out['source_matches'].values()) and out['mode']=='LIVE' and out['heartbeat_age_seconds']<35 and out['services']==['active','active'] and not out['error'] and not out['stopped']
out['stable_checkpoint']=stable;(P/'preservation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
