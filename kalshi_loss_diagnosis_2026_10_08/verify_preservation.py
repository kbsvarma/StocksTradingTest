import json,hashlib,time,subprocess
from pathlib import Path
P=Path(__file__).resolve().parent
expected=json.loads((P/'source_hashes.json').read_text())
assert all(hashlib.sha256(Path(n).read_bytes()).hexdigest()==h for n,h in expected.items())
base=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
bh=json.loads((base/'manifest.json').read_text())['sha256']
assert all(hashlib.sha256((base/'source'/n).read_bytes()).hexdigest()==h for n,h in bh.items())
live=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
s=json.loads((live/'state/status.json').read_text());receipt=json.loads((P/'snapshot_receipt.json').read_text());services={}
for name,old in receipt['services'].items():
 new=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
 assert new==old,(name,new,old)
 services[name]=new
out=dict(ts=time.time(),production_python_hashes_preserved=len(expected),baseline_hashes_preserved=len(bh),services=services,heartbeat_age=time.time()-s['ts'],mode=s['mode'],halted=s.get('halted'),error=s.get('error'),limits={k:s.get(k) for k in ('max_contracts','max_risk','daily_loss_budget')},mutations_to_production=False)
assert out['heartbeat_age']<45
(P/'preservation.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
