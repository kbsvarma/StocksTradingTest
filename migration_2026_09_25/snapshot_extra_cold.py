"""Cold, offline migration evidence. No services started or stopped."""
from pathlib import Path
import os,shutil,sqlite3,subprocess,json,hashlib
HOME=Path('/home/varmak')
OUT=HOME/'migration-extra-20260925-cold'

def verify_frozen():
 if not (HOME/'MIGRATION_SOURCE_FROZEN_20260925').exists():raise RuntimeError('Source shutdown receipt missing')
 receipt=json.loads((HOME/'migration_source_shutdown_20260925.json').read_text())
 for scope in ['system','user']:
  base=['systemctl']+(['--user'] if scope=='user' else [])
  for name in receipt[scope]:
   active=subprocess.run(base+['is-active',name],capture_output=True,text=True).stdout.strip()
   enabled=subprocess.run(base+['is-enabled',name],capture_output=True,text=True).stdout.strip()
   if active not in ['inactive','failed','unknown'] or enabled not in ['disabled','static','not-found','']:raise RuntimeError('Source not frozen: '+name)
verify_frozen()

if OUT.exists():raise SystemExit('Destination exists; inspect before repeating')
OUT.mkdir(mode=0o700)
manifest=[]
def copy(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True)
 if src.is_symlink():
  # Do not silently follow external/private paths.
  manifest.append({'source':str(src),'skipped_symlink':os.readlink(src)});return
 with src.open('rb') as f:head=f.read(16)
 if head==b'SQLite format 3\x00':
  a=sqlite3.connect(src.as_uri()+'?mode=ro',uri=True);b=sqlite3.connect(dst);a.backup(b)
  assert b.execute('pragma integrity_check').fetchone()[0]=='ok';a.close();b.close();method='sqlite_backup'
 else:shutil.copy2(src,dst);method='copy'
 dst.chmod(0o600)
 manifest.append({'path':str(dst.relative_to(OUT)),'method':method,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest(),'bytes':dst.stat().st_size})
roots={'advisor':HOME/'stockstest/advisor','kalshi_crypto':HOME/'kalshi_revalidation_2026_09_12/live_v1'}
for label,root in roots.items():
 for parent,ds,fs in os.walk(root):
  ds[:]=[d for d in ds if d not in ['__pycache__','.venv','.git','.pytest_cache']]
  for n in fs:
   if n.endswith(('-wal','-shm','-journal')) or (n.endswith('.lock') and not n.startswith('requirements')) or n in ['ARM_1CT','ARM_2CT']:continue
   src=Path(parent)/n
   if n.startswith('.env') or n.endswith('.pem'):continue
   copy(src,OUT/label/src.relative_to(root))
for label,py in [('advisor',HOME/'stockstest/.venv/bin/python'),('kalshi_crypto',HOME/'kalshi/venv/bin/python')]:
 freeze=subprocess.check_output([str(py),'-m','pip','freeze','--all'],text=True)
 (OUT/(label+'-runtime.lock')).write_text(freeze)
for f in (HOME/'.config/systemd/user').glob('*'):
 if f.is_file() and (f.name.startswith('advisor-') or f.name.startswith('kalshi-crypto-z2-')):copy(f,OUT/'units'/f.name)
verify_frozen()
(OUT/'manifest.json').write_text(json.dumps({'mode':'cold','source_home':str(HOME),'files':manifest},indent=2))
print(json.dumps({'files':len(manifest),'bytes':sum(r.get('bytes',0) for r in manifest),'sqlite_backups':sum(r.get('method')=='sqlite_backup' for r in manifest)}))
