"""Read-only server source, flags and service process receipt."""
import hashlib, json, subprocess
from pathlib import Path
base = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
result = dict(files={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(base.iterdir()) if p.suffix in ['.py', '.css']},
              flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((base/'state').iterdir())
                     if p.is_file() and (p.name.startswith('ARM') or p.name.startswith('HALT'))},
              services=subprocess.check_output(['systemctl','--user','show',
                'kalshi-crypto-z2-live.service','kalshi-dashboard.service',
                'kalshi-execution-observer.service','kalshi-tennis90-paper.service',
                'kalshi-resting-paper.service','-p','Id','-p','ActiveState',
                '-p','MainPID','-p','NRestarts'],text=True))
print(json.dumps(result, indent=2))
