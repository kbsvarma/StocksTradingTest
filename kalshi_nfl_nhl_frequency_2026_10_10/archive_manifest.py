"""Hash immutable research inputs/outputs. No network or live application access."""
from pathlib import Path
import hashlib,json
P=Path(__file__).resolve().parent
skip={'SHA256SUMS.txt','archive_receipt.json','local_verification.json','.request_clock','.request_lock'}
rows=[]
for p in sorted(P.rglob('*')):
    if not p.is_file() or p.name in skip or '__pycache__' in p.parts:
        continue
    data=p.read_bytes()
    rows.append({'path':str(p.relative_to(P)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
(P/'SHA256SUMS.txt').write_text(''.join(r['sha256']+'  '+r['path']+'\n' for r in rows))
(P/'archive_receipt.json').write_text(json.dumps({'files':len(rows),'bytes':sum(r['bytes'] for r in rows),'records':rows},indent=2)+'\n')
print(json.dumps({'files':len(rows),'bytes':sum(r['bytes'] for r in rows)}))
