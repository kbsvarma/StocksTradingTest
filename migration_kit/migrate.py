#!/usr/bin/env python3
"""Trend-only migration snapshots and offline restore staging. Never starts services."""
import argparse,hashlib,json,os,platform,re,shutil,sqlite3,stat,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
SYSTEM=('trendbot.service','trendbot-ibc.service','trendbot-xvfb.service','trendbot-dashboard.service','trendbot-watchdog.service','trendbot-watchdog.timer')
USER=('trendbot-shadow.service','trendbot-shadow.timer','trendbot-shadow-capture.service','trendbot-shadow-capture.timer')
ROOTS=('trendbot','Jts/ibgateway','Jts/jars','ibc')
PRIVATE=('.trendbot_env','ibc/config.ini','Jts/jts.ini','Jts/xmlopt.dat')
SKIP={'.venv','__pycache__','.pytest_cache','.mypy_cache','node_modules'}
def command(args):
 p=subprocess.run(args,text=True,capture_output=True);return p.returncode,p.stdout.strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def unit_state(user,name):
 base=['systemctl']+(['--user'] if user else []);_,active=command(base+['is-active',name]);_,enabled=command(base+['is-enabled',name]);return dict(unit=name,scope='user' if user else 'system',active=active,enabled=enabled)
def inventory(home):
 repo=home/'trendbot/trend_bot';rc,commit=command(['git','-C',str(repo),'rev-parse','HEAD']);rc2,dirty=command(['git','-C',str(repo),'status','--porcelain','--untracked-files=no']);units=[unit_state(False,n) for n in SYSTEM]+[unit_state(True,n) for n in USER];py=repo/'.venv/bin/python';rc3,pv=command([str(py),'-V']) if py.exists() else (1,'missing');return dict(schema=1,scope='trend_and_shadows_only',advisor='DEFERRED: neither inspected further nor packaged',created_at=datetime.now(timezone.utc).isoformat(),source_host=platform.node(),source_home=str(home),source_user=home.name,architecture=platform.machine(),python=pv,commit=commit,git_clean=rc==0 and rc2==0 and not dirty,units=units,roots={r:(home/r).exists() for r in ROOTS},private_files={r:(home/r).exists() for r in PRIVATE},gateway_shared_with_advisor=True)
def copy_one(src,dst,home,payload,method):
 if src.is_symlink():
  target=Path(os.readlink(src));resolved=(src.parent/target).resolve() if not target.is_absolute() else target.resolve()
  try:rel=resolved.relative_to(home)
  except ValueError:raise ValueError(f'external symlink needs explicit migration handling: {src}')
  dst.parent.mkdir(parents=True,exist_ok=True);os.symlink(os.path.relpath(payload/rel,dst.parent),dst);return 'symlink'
 dst.parent.mkdir(parents=True,exist_ok=True)
 with src.open('rb') as f:header=f.read(16)
 if header==b'SQLite format 3\x00':
  source=sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=30);target=sqlite3.connect(dst)
  try:source.backup(target);assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  finally:target.close();source.close()
  shutil.copymode(src,dst);return 'sqlite_backup'
 shutil.copy2(src,dst);return method

def snapshot(home,out,mode):
 home=home.resolve();out=out.resolve();inv=inventory(home)
 if mode=='cold':
  blockers=[u for u in inv['units'] if u['active'] not in ['inactive','failed','unknown'] or u['enabled'] not in ['disabled','masked','static','not-found','']]
  if blockers:raise ValueError('cold snapshot requires stopped/disabled Trend services and timers, including shared gateway; coordinate Advisor first')
 if not inv['git_clean']:raise ValueError('source Trend tracked tree is dirty; preserve/review release before packaging')
 if out.exists():raise ValueError('output must not exist')
 if any(out.is_relative_to(home/r) for r in ROOTS):raise ValueError('output cannot be inside a captured source tree')
 out.mkdir(parents=True,mode=0o700);payload=out/'home';payload.mkdir();records=[];unavailable_links=[]
 try:
  for root in ROOTS:
   base=home/root
   if not base.exists():raise ValueError(f'required source missing: {root}')
   for directory,dirs,files in os.walk(base,followlinks=False):
    dirs[:]=[d for d in dirs if d not in SKIP];directory=Path(directory)
    # Preserve symlink directories as links; never walk through them.
    for name in list(dirs):
     if (directory/name).is_symlink():files.append(name);dirs.remove(name)
    (payload/directory.relative_to(home)).mkdir(parents=True,exist_ok=True)
    for name in files:
     src=directory/name;rel=src.relative_to(home)
     if src.is_symlink() and not src.exists():
      unavailable_links.append(dict(path=str(rel),target=os.readlink(src),reason='already missing on source; no data to transfer'));continue
     if str(rel) in PRIVATE or name.endswith(('-wal','-shm','-journal')) or (name.endswith('.lock') and 'state' in rel.parts):continue
     dst=payload/rel;method=copy_one(src,dst,home,payload,'copy');records.append(dict(path='home/'+str(rel),method=method,sha256=hashlib.sha256(os.readlink(dst).encode()).hexdigest() if dst.is_symlink() else sha(dst),mode=stat.S_IMODE(dst.lstat().st_mode),bytes=dst.lstat().st_size))
  for scope,names,base in [('system',SYSTEM,Path('/etc/systemd/system')),('user',USER,home/'.config/systemd/user')]:
   for name in names:
    src=base/name
    if not src.exists():raise ValueError(f'missing installed unit: {src}')
    dst=out/'units'/scope/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);records.append(dict(path=str(dst.relative_to(out)),method='unit',sha256=sha(dst),mode=stat.S_IMODE(dst.stat().st_mode),bytes=dst.stat().st_size))
  rc,freeze=command([str(home/'trendbot/trend_bot/.venv/bin/python'),'-m','pip','freeze','--all'])
  if rc:raise ValueError('could not capture actual Python environment')
  if any('@ file:' in line or line.startswith('-e ') for line in freeze.splitlines()):raise ValueError('runtime contains local/editable packages; resolve before migration')
  lock=out/'runtime.lock';lock.write_text(freeze+'\n');records.append(dict(path='runtime.lock',method='runtime_freeze',sha256=sha(lock),mode=0o600,bytes=lock.stat().st_size));lock.chmod(0o600)
  if mode=='cold':
   after=inventory(home)
   if after['units']!=inv['units'] or after['commit']!=inv['commit'] or not after['git_clean']:raise ValueError('source activity/release changed during cold capture')
  inv.update(mode=mode,activation_eligible=mode=='cold',consistency='quiesced cross-file copy' if mode=='cold' else 'SQLite-consistent per database; cross-file live snapshot NOT a cutover image',excluded_private=list(PRIVATE),unavailable_links=unavailable_links,files=records)
  (out/'manifest.json').write_text(json.dumps(inv,indent=2));(out/'manifest.sha256').write_text(sha(out/'manifest.json')+'\n');(out/'manifest.json').chmod(0o600)
  verify(out);print(json.dumps(dict(bundle=str(out),mode=mode,files=len(records),bytes=sum(r['bytes'] for r in records),sqlite_backups=sum(r['method']=='sqlite_backup' for r in records),advisor='excluded')))
 except Exception:
  (out/'INCOMPLETE').write_text('Do not restore: capture did not complete.');raise

def verify(bundle):
 if (bundle/'INCOMPLETE').exists():raise ValueError('incomplete bundle')
 if sha(bundle/'manifest.json')!=(bundle/'manifest.sha256').read_text().strip():raise ValueError('manifest checksum mismatch')
 m=json.loads((bundle/'manifest.json').read_text())
 for r in m['files']:
  rel=Path(r['path'])
  if rel.is_absolute() or '..' in rel.parts:raise ValueError('unsafe manifest path')
  p=bundle/rel
  if p.is_symlink() and not p.resolve().is_relative_to(bundle.resolve()):raise ValueError('symlink escapes bundle')
  digest=hashlib.sha256(os.readlink(p).encode()).hexdigest() if p.is_symlink() else sha(p)
  if digest!=r['sha256']:raise ValueError(f'checksum mismatch: {r["path"]}')
  if r['method']=='sqlite_backup':
   db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);ok=db.execute('PRAGMA integrity_check').fetchone()[0];db.close()
   if ok!='ok':raise ValueError(f'invalid sqlite: {p}')
 return m

def stage(bundle,dest,target_home,target_user):
 bundle=bundle.resolve();m=verify(bundle)
 if dest.exists():raise ValueError('stage destination must not exist')
 if not re.fullmatch(r'[a-z_][a-z0-9_-]*',target_user):raise ValueError('invalid Linux username')
 if not target_home.startswith('/') or re.search(r'[\s\x00-\x1f]',target_home):raise ValueError('target home must be an absolute path without whitespace')
 dest.mkdir(parents=True,mode=0o700);shutil.copytree(bundle/'home',dest/'home',symlinks=True);shutil.copytree(bundle/'units',dest/'units');shutil.copy2(bundle/'runtime.lock',dest/'runtime.lock')
 # Only non-repository deployment wrappers and unit paths are rendered.
 edited=[]
 for f in [*dest.glob('units/*/*'),*(dest/'home/trendbot').glob('*.sh')]:
  text=f.read_text();new=text.replace(m['source_home'],target_home)
  if f.parent.name=='system':new=re.sub(r'^User=.*$',f'User={target_user}',new,flags=re.M)
  if new!=text:f.write_text(new);edited.append(str(f.relative_to(dest)))
 manifest=dict(bundle_manifest_sha256=sha(bundle/'manifest.json'),source_mode=m['mode'],activation_eligible=m['activation_eligible'],target_home=target_home,target_user=target_user,rendered_files=edited,advisor='not included',python=m['python'],architecture=m['architecture'],source_host=m['source_host'])
 (dest/'stage.json').write_text(json.dumps(manifest,indent=2));(dest/'SOURCE_MANIFEST.json').write_text(json.dumps(m,indent=2))
 # Frozen code and shadow hashes must remain byte-for-byte intact after rendering.
 release=dest/'home/trendbot/shadow_capture/release.json'
 if release.exists():
  for name,h in json.loads(release.read_text()).items():
   if sha(release.parent/name)!=h:raise ValueError(f'frozen shadow changed during stage: {name}')
 repo=dest/'home/trendbot/trend_bot';rc,dirty=command(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'])
 if rc or dirty:raise ValueError('staged bot release differs from clean source')
 print(json.dumps(dict(stage=str(dest),rendered_files=len(edited),shadow_hashes='pass',trend_git='clean',services_started=False,mode=m['mode'])))

def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 i=sub.add_parser('inventory');i.add_argument('--home',type=Path,default=Path.home());i.add_argument('--output',type=Path,required=True)
 s=sub.add_parser('snapshot');s.add_argument('--home',type=Path,default=Path.home());s.add_argument('--output',type=Path,required=True);s.add_argument('--mode',choices=['warm','cold'],default='warm')
 v=sub.add_parser('verify');v.add_argument('bundle',type=Path)
 t=sub.add_parser('stage');t.add_argument('bundle',type=Path);t.add_argument('--destination',type=Path,required=True);t.add_argument('--target-home',required=True);t.add_argument('--target-user',required=True)
 a=p.parse_args()
 if a.command=='inventory':a.output.write_text(json.dumps(inventory(a.home),indent=2))
 elif a.command=='snapshot':snapshot(a.home,a.output,a.mode)
 elif a.command=='verify':m=verify(a.bundle.resolve());print(json.dumps(dict(verified=True,files=len(m['files']),mode=m['mode'])))
 elif a.command=='stage':stage(a.bundle,a.destination,a.target_home,a.target_user)
if __name__=='__main__':main()
