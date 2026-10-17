"""Export only OncoPulse for migration. Leave services and /home/ashish untouched."""
from pathlib import Path
import os,sys,stat,json,shutil,sqlite3,hashlib,pwd,tarfile,time,contextlib,re,uuid,fcntl
if os.geteuid()!=0:raise SystemExit('Run this focused export with sudo on the old server.')
owner=pwd.getpwnam('varmak');base=Path('/home/varmak/.data_migration_control_20260926')
lock=(base/'oncopulse_export.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit('An OncoPulse export is already running.')
run_id=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
out=base/('oncopulse_export_'+run_id);out.mkdir(mode=0o700)
os.chown(out,owner.pw_uid,owner.pw_gid)
def progress(stage,**details):
 data={'time':time.time(),'run_id':run_id,'stage':stage,**details}
 p=base/'oncopulse_export_progress.json';tmp=p.with_suffix('.tmp')
 tmp.write_text(json.dumps(data));tmp.chmod(0o600);os.chown(tmp,owner.pw_uid,owner.pw_gid);tmp.replace(p)
 print(json.dumps(data),flush=True)
progress('copying')
entries=0

roots=['/home/oncopulse-deployment','/var/lib/oncopulse','/var/cache/oncopulse','/var/log/oncopulse','/etc/systemd/system/oncopulse.service','/etc/nginx/sites-available/streamlit']
unit=Path('/etc/systemd/system/oncopulse.service').read_text()
for line in unit.splitlines():
 if line.startswith('EnvironmentFile='):
  for value in line.partition('=')[2].split():
   value=value.lstrip('-').strip('"')
   if value.startswith('/') and not value.startswith('/home/ashish/'):roots.append(value)
# Preserve this site's certificate files and renewal configuration only.
nginx=Path('/etc/nginx/sites-available/streamlit').read_text()
for value in re.findall(r'^\s*ssl_certificate(?:_key)?\s+([^;\s]+)\s*;',nginx,re.M):
 p=Path(value)
 if str(p).startswith(('/etc/letsencrypt/','/etc/ssl/')) and p.exists():
  roots.extend([str(p),str(p.resolve())])
  if str(p).startswith('/etc/letsencrypt/live/'):
   renewal=Path('/etc/letsencrypt/renewal')/(p.parent.name+'.conf')
   if renewal.is_file():roots.append(str(renewal))
records=[];databases=[];errors=[];links=[]
def copy_file(src,dst):
 before=src.stat();dst.parent.mkdir(parents=True,exist_ok=True)
 with src.open('rb') as f:header=f.read(16)
 if header==b'SQLite format 3\x00':
  with contextlib.closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=30)) as a:
   with contextlib.closing(sqlite3.connect(str(dst))) as b:
    a.backup(b);checks=b.execute('PRAGMA integrity_check').fetchall()
    if checks!=[('ok',)]:raise RuntimeError('Database integrity check failed: '+str(src))
  shutil.copystat(src,dst);databases.append(str(src))
 else:
  shutil.copy2(src,dst);after=src.stat()
  if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Source changed during export: '+str(src))
for value in dict.fromkeys(roots):
 progress('copying',root=value,entries=entries)
 src=Path(value)
 if not src.exists():continue
 paths=[src]
 if src.is_dir():
  for parent,dirs,files in os.walk(src,followlinks=False):paths.extend(Path(parent)/n for n in dirs+files)
 for p in paths:
  entries+=1
  if entries%500==0:progress('copying',entries=entries)
  dst=out/'files'/str(p).lstrip('/');s=p.lstat()
  if stat.S_ISLNK(s.st_mode):
   dst.parent.mkdir(parents=True,exist_ok=True);target=os.readlink(p);dst.symlink_to(target);links.append({'path':str(p),'target':target})
  elif stat.S_ISDIR(s.st_mode):dst.mkdir(parents=True,exist_ok=True)
  elif stat.S_ISREG(s.st_mode):
   # Snapshot SQLite main files using the backup API; companion journals are not copied.
   companion=False
   for suffix in ['-wal','-shm','-journal']:
    if str(p).endswith(suffix):
     main=Path(str(p)[:-len(suffix)])
     if main.is_file() and not main.is_symlink():
      with main.open('rb') as f:companion=f.read(16)==b'SQLite format 3\x00'
   if not companion:copy_file(p,dst)
progress('hashing_files',entries=entries)
for p in (out/'files').rglob('*'):
 if not p.is_symlink() and p.is_file():
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
  if len(records)%500==0:progress('hashing_files',files=len(records))
  records.append({'path':str(p.relative_to(out/'files')),'bytes':p.stat().st_size,'sha256':h.hexdigest()})
(out/'manifest.json').write_text(json.dumps({'created_at':time.time(),'roots':roots,'files':records,'sqlite_snapshots':databases,'symlinks':links,'source_services_unchanged':True,'excluded':['/home/ashish']},indent=2))
progress('compressing',files=len(records))
archive=base/('oncopulse_export_'+run_id+'.tar.gz')
with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
 for index,p in enumerate([out,*out.rglob('*')]):
  if index%500==0:progress('compressing',entries=index,archive_bytes=archive.stat().st_size)
  tar.add(p,arcname=str(Path('oncopulse_export')/p.relative_to(out)),recursive=False)
progress('verifying_archive',archive_bytes=archive.stat().st_size)
# Read to the gzip trailer to detect a truncated archive before publishing a receipt.
import gzip
with gzip.open(archive,'rb') as f:
 while f.read(8*1024**2):pass

h=hashlib.sha256()
with archive.open('rb') as f:
 for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
receipt=base/'oncopulse_export_receipt.json';receipt.write_text(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':h.hexdigest(),'files':len(records),'databases':databases},indent=2))
for p in [out,*out.rglob('*'),archive,receipt]:
 os.chown(p,owner.pw_uid,owner.pw_gid,follow_symlinks=False)
archive.chmod(0o600);receipt.chmod(0o600)
progress('complete',archive=str(archive),bytes=archive.stat().st_size,files=len(records))
print(receipt.read_text(),flush=True)
