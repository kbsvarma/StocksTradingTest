from pathlib import Path
import json,hashlib
root=Path('/home/varmak/.data_migration_control_20260926/extended_audit')
rows=[json.loads(l) for l in (root/'user/files.jsonl').open()]
selected=[]
for r in rows:
 p=Path(r['path'])
 if r['kind']!='file' or r['bytes']==0:continue
 relevant=(r['uid']==1002 and p.parent==Path('/tmp') and p.name!='codex_inventory_20260926.py') or (str(p).startswith('/opt/gemma-chat/') and not any(x in p.parts for x in ['.venv','.idea','.pytest_cache','.ruff_cache','__pycache__']) and p.name not in ['.env','.env.example']) or str(p)=='/var/lib/oncopulse/oncopulse.db' or (r['uid']==1002 and str(p).startswith(('/tmp/shadow-check-','/tmp/claude-1002/')))
 if not relevant:continue
 try:
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1024**2),b''):h.update(b)
  r['sha256']=h.hexdigest();selected.append(r)
 except OSError as e:r['error']=str(e);selected.append(r)
(root/'candidate_hashes.json').write_text(json.dumps(selected,indent=2))
print(json.dumps({'candidates':len(selected),'bytes':sum(x['bytes'] for x in selected)}))
