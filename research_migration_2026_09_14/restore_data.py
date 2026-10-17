"""Restore selected offloaded data by relative file/directory prefix, checking space and SHA256."""
from pathlib import Path
import argparse,json,subprocess,hashlib,shutil,shlex
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'research_migration_2026_09_14'
p=argparse.ArgumentParser();p.add_argument('prefix');p.add_argument('--list',action='store_true');args=p.parse_args()
prefix=args.prefix.rstrip('/');assert not prefix.startswith('/') and '..' not in Path(prefix).parts
rows=[json.loads(s) for s in (OUT/'removed.jsonl').read_text().splitlines()];rows=[r for r in rows if r['path']==prefix or r['path'].startswith(prefix+'/')]
if not rows:raise SystemExit('No migrated data match this relative path')
missing=[r for r in rows if not (ROOT/r['path']).exists()];total=sum(r['size'] for r in missing)
print(f'{len(rows)} matching files; {len(missing)} absent locally; {total/1024**3:.3f} GiB to restore')
if args.list or not missing:raise SystemExit(0)
if shutil.disk_usage(ROOT).free<total+1024**3:raise SystemExit('Not enough local space, including a 1 GiB reserve')
subprocess.run(['rsync','-aR','--ignore-existing','--from0','--files-from=-','-e',shlex.join(['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes']),'varma@192.168.3.136:/home/varma/research_data/StocksTradingTest/',str(ROOT)+'/'],input=b''.join(r['path'].encode()+b'\0' for r in missing),check=True)
for r in missing:
 h=hashlib.sha256()
 with (ROOT/r['path']).open('rb') as f:
  for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
 assert h.hexdigest()==r['sha256'],'Checksum mismatch: '+r['path']
print('Restored and checksum-verified.')
