"""Hash study and referenced frozen inputs after completion."""
import datetime as dt
import hashlib
import json
from pathlib import Path
P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_noncrypto15m_2026_10_07'
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
frozen=json.loads((P/'feature_validation.json').read_text())['input_sha256']
assert all(digest(OLD/n)==v for n,v in frozen.items())
files={str(p.relative_to(P)):digest(p) for p in sorted(P.rglob('*'))
       if p.is_file() and '__pycache__' not in p.parts and p.name!='manifest.json'}
(P/'manifest.json').write_text(json.dumps(dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),
    sha256=files,input_sha256=frozen,no_live_changes=True,
    input_root=str(OLD),reused_history_not_independent=True),indent=2)+'\n')
print('Hashed',len(files),'files; all frozen research inputs unchanged.')
