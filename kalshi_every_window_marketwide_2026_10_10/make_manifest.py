"""Freeze research input/code identity; no credentials or production paths."""
from common import *
from datetime import timezone
import hashlib
roots=[P,P.parent/'kalshi_nfl_nhl_frequency_2026_10_10/data',P.parent/'kalshi_noncrypto_consistency_2026_10_10/financial',P.parent/'kalshi_noncrypto_consistency_2026_10_10/sports',P.parent/'kalshi_weather_validation_2026_10_10/data']
extra=[P.parent/'kalshi_noncrypto_consistency_2026_10_10/milestones_all.json']
for d in ['kalshi_noncrypto15m_2026_10_07','kalshi_broad_review_2026_10_10/extension']:
 for name in ['features.pickle','underlying_minutes.pickle']:extra.append(P.parent/d/name)
paths=set(extra)
for root in roots:
 for p in root.rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.log','.pyc','.tmp') and p.name not in ('MANIFEST.json.gz','request_clock'):paths.add(p)
files=[]
for p in sorted(paths):
 if not p.exists():continue
 h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(1024*1024):h.update(b)
 files.append({'path':str(p),'bytes':p.stat().st_size,'sha256':h.hexdigest()})
save(P/'MANIFEST.json.gz',{'created_utc':datetime.now(timezone.utc).isoformat(),'files':files,'notes':'New study content plus read-only primary input dependencies. The source collectors also used previous broad-review catalogues; final catalogues and quote payloads were copied into this study. Historical source archives stay at these recorded new-server paths.'})
print(json.dumps({'files':len(files),'bytes':sum(r['bytes'] for r in files),'manifest':str(P/'MANIFEST.json.gz')}))
