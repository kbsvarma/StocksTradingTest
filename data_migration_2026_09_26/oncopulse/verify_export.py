from pathlib import Path,PurePosixPath
import os,json,tarfile,hashlib,sqlite3,contextlib
B=Path('/home/varma/old_server_archive_2026_09_26/additional_projects/oncopulse/full_export')
r=json.loads((B/'export_receipt.json').read_text());archive=B/Path(r['archive']).name
h=hashlib.sha256()
with archive.open('rb') as f:
 for chunk in iter(lambda:f.read(8*1024**2),b''):h.update(chunk)
assert h.hexdigest()==r['sha256'],'Archive checksum mismatch'
D=B/'extracted';D.mkdir(mode=0o700,exist_ok=False)
def safe(name):
 p=PurePosixPath(name)
 assert not p.is_absolute() and '..' not in p.parts and p.parts[0]=='oncopulse_export',name
 return D.joinpath(*p.parts)
with tarfile.open(archive,'r:gz') as t:
 members=t.getmembers()
 for m in members:
  p=safe(m.name)
  assert m.isdir() or m.isfile() or m.issym() or m.islnk(),m.name
  if m.isdir():p.mkdir(parents=True,exist_ok=True)
  elif m.isfile():
   p.parent.mkdir(parents=True,exist_ok=True)
   with t.extractfile(m) as src,p.open('xb') as dst:
    import shutil;shutil.copyfileobj(src,dst)
   p.chmod(m.mode & 0o777)
 pending=[m for m in members if m.islnk()]
 while pending:
  following=[]
  for m in pending:
   target=safe(m.linkname)
   if target.is_file():os.link(target,safe(m.name))
   else:following.append(m)
  assert len(following)<len(pending),'Unresolved archive hardlink'
  pending=following
 for m in members:
  if m.issym():
   p=safe(m.name);p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to(m.linkname)
E=D/'oncopulse_export';manifest=json.loads((E/'manifest.json').read_text());errors=[]
for rec in manifest['files']:
 p=E/'files'/rec['path'];h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
 if p.stat().st_size!=rec['bytes'] or h.hexdigest()!=rec['sha256']:errors.append(rec['path'])
assert not errors,errors
checks=[]
for name in manifest['sqlite_snapshots']:
 p=E/'files'/name.lstrip('/')
 with contextlib.closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as c:
  assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
  tables=[x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'") if not x[0].startswith('sqlite_')]
  counts={n:c.execute('SELECT COUNT(*) FROM "'+n.replace('"','""')+'"').fetchone()[0] for n in tables}
  checks.append({'path':name,'integrity':'ok','table_counts':counts})
result={'archive_sha256':h.hexdigest() if False else r['sha256'],'files_verified':len(manifest['files']),'errors':errors,'databases':checks,'source_roots':manifest['roots'],'excluded':manifest['excluded']}
(B/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
