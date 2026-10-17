import hashlib,json,subprocess,time,sys
from pathlib import Path
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
status=json.loads((L/'state/status.json').read_text())
out=dict(ts=time.time(),source={p.name:sha(p) for p in L.glob('*.py')},flags={p.name:sha(p) for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True),status={k:status.get(k) for k in ('ts','mode','armed','contracts','max_contracts','max_risk','daily_loss_budget','account')})
(P/(sys.argv[1]+'.json')).write_text(json.dumps(out,indent=2))
print(json.dumps(out['status']))
