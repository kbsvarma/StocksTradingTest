"""Read-only production fingerprints; execute remotely via stdin."""
import hashlib,json,subprocess,time
from pathlib import Path
L=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
names=('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service')
print(json.dumps(dict(ts=time.time(),hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in L.iterdir() if f.suffix in ('.py','.json') or f.name=='dashboard.css'},flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))},services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in names}),indent=2))
