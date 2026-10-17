"""Final source/configuration/record-preservation audit. No exchange calls."""
from pathlib import Path
import json,hashlib,sqlite3,time,subprocess
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
original=json.loads((P/'original_hashes.json').read_text());expected=dict(original)
for phase in ('','phase2_','phase3_','phase4_'):expected.update(json.loads((P/(phase+'deployment_receipt.json')).read_text())['sha256'])
actual={p.name:sha(p) for p in L.iterdir() if p.suffix=='.py' or p.name=='dashboard.css'}
assert actual==expected,'Unexpected production change'
checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28';manifest=json.loads((checkpoint/'manifest.json').read_text())['sha256'];assert all(sha(checkpoint/'source'/n)==h for n,h in manifest.items())
pre=json.loads((P/'preflight.json').read_text());s=json.loads((L/'state/status.json').read_text());assert s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('error','halted','stopped'))
flags={f.name:sha(f) for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))};assert flags==pre['flags']
# The invariant fields are identical to the deployment helper's configuration projection.
config=dict(settings={k:s[k] for k in pre['config']['settings']},assets={a:{k:s['assets'][a][k] for k in v} for a,v in pre['config']['assets'].items()},lanes={n:{k:s['lanes'][n][k] for k in v} for n,v in pre['config']['lanes'].items()});assert config==pre['config']
with sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 orders=dict(db.execute('select ticker,record from orders'));attempts=dict(db.execute('select client_id,record from order_attempts'))
 oldorders=json.loads((P/'preserved_orders.json').read_text());oldattempts=json.loads((P/'preserved_attempts.json').read_text())
 assert all(orders.get(k)==v for k,v in oldorders.items());assert all(attempts.get(k)==v for k,v in oldattempts.items())
services={}
for n,previous in pre['services'].items():
 current=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines());services[n]=current
 if n!='kalshi-crypto-z2-live.service':assert current==previous
report=dict(ts=time.time(),source_changes=[n for n in original if actual[n]!=original[n]],all_other_sources_unchanged=True,flags_unchanged=True,configuration_unchanged=True,stable_checkpoint_files=len(manifest),preserved_market_rows=len(oldorders),preserved_attempts=len(oldattempts),services=services,heartbeat_age=time.time()-s['ts'])
(P/'evidence/final_preservation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
