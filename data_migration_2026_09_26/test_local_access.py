"""Exercise the updated remote runner and restore helper without repopulating local caches."""
from pathlib import Path
import subprocess,json,tempfile,shutil,hashlib
ROOT=Path(__file__).resolve().parent.parent
helper=ROOT/'research_migration_2026_09_14'
r=subprocess.check_output(['python3',str(helper/'run_remote.py'),'-c','import os,sys; print(os.getcwd()); print(sys.executable)'],text=True)
assert '/home/varma/' in r and '/home/varma/research_runtime/bin/python' in r,r
print(r.strip())
rows=[json.loads(line) for line in (helper/'removed.jsonl').open()]
row=next(r for r in rows if 100<r['size']<10000 and '..' not in Path(r['path']).parts and not r['path'].startswith('/'))
with tempfile.TemporaryDirectory(prefix='research-restore-smoke-') as tmp:
 base=Path(tmp);h=base/'research_migration_2026_09_14';h.mkdir()
 shutil.copy2(helper/'restore_data.py',h/'restore_data.py')
 (h/'removed.jsonl').write_text(json.dumps(row)+'\n')
 subprocess.run(['python3',str(h/'restore_data.py'),row['path']],check=True)
 actual=hashlib.sha256((base/row['path']).read_bytes()).hexdigest();assert actual==row['sha256']
 result={'remote_runner':'passed','restore_helper':'passed','sample_path':row['path'],'sha256':actual,'temporary_local_restore_removed':True}
(ROOT/'data_migration_2026_09_26/local_access_verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
