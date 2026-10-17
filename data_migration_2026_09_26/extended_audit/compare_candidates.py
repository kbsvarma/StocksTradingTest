from pathlib import Path
import os,json,hashlib,collections
H=Path('/home/varma');C=H/'.data_migration_control_20260926';A=H/'old_server_archive_2026_09_26'
rows=json.loads((C/'external_candidate_hashes.json').read_text());by_size=collections.defaultdict(set)
for r in rows:
 if 'sha256' in r:by_size[r['bytes']].add(r['sha256'])
matches=collections.defaultdict(list);checked=0;errors=[]
def check(p,size):
 global checked
 if size not in by_size:return
 try:
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1024**2),b''):h.update(b)
  digest=h.hexdigest();checked+=1
  if digest in by_size[size]:matches[digest].append(str(p))
 except OSError as e:errors.append({'path':str(p),'error':str(e)})
for line in (A/'audit/final_inventory/files.jsonl').open():
 r=json.loads(line)
 if r['kind']=='file' and r['bytes'] in by_size:check(A/'home/varmak'/r['path'],r['bytes'])
for parent,dirs,files in os.walk(H,followlinks=False):
 if Path(parent)==H:dirs[:]=[d for d in dirs if d not in ['old_server_archive_2026_09_26','.data_migration_control_20260926']]
 for name in files:
  p=Path(parent)/name
  try:
   if not p.is_symlink():check(p,p.stat().st_size)
  except OSError as e:errors.append({'path':str(p),'error':str(e)})
result={'new_server_matching_size_files_hashed':checked,'errors':errors,'scope':'Verified archived account and accessible current /home/varma files; exact SHA256 comparisons','candidates':[dict(r,new_server_matches=matches.get(r.get('sha256'),[])) for r in rows]}
(C/'external_candidate_comparison.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'checked':checked,'errors':len(errors),'candidates':len(rows),'without_exact_copy':sum(not r['new_server_matches'] for r in result['candidates'])}))
