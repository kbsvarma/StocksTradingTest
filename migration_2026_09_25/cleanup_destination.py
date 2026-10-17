#!/usr/bin/env python3
"""Remove destination Docker/OpenStaff workloads; preserve every backup mount.
User-authorized cleanup. Does not install or start migration applications.
"""
import argparse,json,os,subprocess,shutil,socket,time
from pathlib import Path
PROTECTED=[Path('/backup'),Path('/backups'),Path('/var/backups')]
BACKUP=Path('/var/backups/openstaff-ats/archives')
UUID='90e9596d-883c-4cea-971f-2b34d903b0fe'
TARGETS=[Path('/opt'),Path('/var/lib/docker'),Path('/var/lib/containerd')]
PACKAGES=['docker-ce','docker-ce-cli','docker-ce-rootless-extras','docker-buildx-plugin','docker-compose-plugin','docker-model-plugin','docker.io','docker-cli','containerd.io','containerd','runc']
def run(args,check=True):return subprocess.run(args,text=True,capture_output=True,check=check)
def mounts():return json.loads(run(['findmnt','--json','--list','--output','TARGET,SOURCE,UUID']).stdout)['filesystems']
def backup_ok():
 rows=[r for r in mounts() if r['target']==str(BACKUP)]
 if len(rows)!=1 or rows[0].get('uuid')!=UUID:raise RuntimeError('Protected backup mount identity changed; stopping.')
def guarded_remove(path):
 # Never follow symlinks. Refuse mounted trees, including same-filesystem bind mounts.
 if path.is_symlink():path.unlink();return
 resolved=path.resolve()
 if any(resolved==p or resolved.is_relative_to(p) or p.is_relative_to(resolved) for p in PROTECTED):raise RuntimeError('Protected path: '+str(path))
 if not any(path==p or path.is_relative_to(p) for p in TARGETS):raise RuntimeError('Outside cleanup scope: '+str(path))
 for m in mounts():
  target=Path(m['target'])
  if target==path or target.is_relative_to(path):raise RuntimeError('Mounted tree must not be deleted: '+str(target))
 if path.is_dir():shutil.rmtree(path)
 elif path.exists():path.unlink()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 if socket.gethostname()!='ashish-home':raise SystemExit('Wrong host; refusing.')
 if os.geteuid()!=0:raise SystemExit('Root required. Run from a root shell.')
 backup_ok()
 audit=Path('/root')/('migration-cleanup-'+str(int(time.time())));audit.mkdir(mode=0o700)
 units=[l.split()[0] for l in run(['systemctl','list-unit-files','--no-legend','--no-pager']).stdout.splitlines() if l.startswith('openstaff')]
 ids=run(['docker','ps','-aq']).stdout.split()
 info=json.loads(run(['docker','inspect','--format','{{json .Mounts}}',*ids]).stdout.splitlines()[0]) if len(ids)==1 else None
 containers=[]
 for cid in ids:
  r=json.loads(run(['docker','inspect',cid]).stdout)[0]
  containers.append({k:r.get(k) for k in ['Id','Name','Mounts']})
 packages=[]
 for name in PACKAGES:
  q=run(['dpkg-query','-W','-f=${Status}',name],check=False)
  if q.returncode==0 and q.stdout=='install ok installed':packages.append(name)
 plan={'host':socket.gethostname(),'protected':[str(p) for p in PROTECTED],'backup_uuid':UUID,'units':units,'containers':containers,'packages':packages,'delete_contents_of_opt':True,'delete_runtime_storage':[str(p) for p in TARGETS[1:]]}
 (audit/'plan.json').write_text(json.dumps(plan,indent=2));print('Audit:',audit,flush=True)
 print('Containers:',len(ids),'OpenStaff units:',len(units),'Packages:',', '.join(packages),flush=True)
 print('Protected:',BACKUP,UUID,flush=True)
 if not args.execute:print('Read-only inventory complete. Pass --execute to clean.');return
 # Stop timers/sockets before services so they cannot relaunch removed applications.
 for unit in sorted(units,key=lambda u:0 if u.endswith(('.timer','.socket')) else 1):
  if '@.' in unit:continue
  run(['systemctl','disable','--now',unit],check=False)
 # Refuse deletion if an old controller is still running.
 for unit in units:
  if '@.' not in unit and run(['systemctl','is-active','--quiet',unit],check=False).returncode==0:raise RuntimeError('Still active: '+unit)
 ids=run(['docker','ps','-aq']).stdout.split()
 if ids:
  run(['docker','update','--restart=no',*ids])
  run(['docker','rm','-f',*ids]) # No -v: never touch external/bind-mounted volume contents.
 if run(['docker','ps','-aq']).stdout.strip():raise RuntimeError('Containers remain')
 run(['systemctl','disable','--now','docker.socket','docker.service','containerd.service'])
 backup_ok()
 # Runtime overlay mounts must be gone before any filesystem removal.
 for parent in TARGETS:
  for m in mounts():
   target=Path(m['target'])
   if target==parent or target.is_relative_to(parent):raise RuntimeError('Unexpected surviving mount: '+str(target))
 if packages:
  r=run(['apt-get','purge','-y',*packages],check=False)
  (audit/'package-removal.log').write_text(r.stdout+r.stderr)
  if r.returncode:raise RuntimeError('Package removal failed; see audit log')
 for child in list(Path('/opt').iterdir()):guarded_remove(child)
 for p in TARGETS[1:]:guarded_remove(p)
 # Remove only explicitly identified application service definitions, not backups or OS services.
 for unit in units:
  p=Path('/etc/systemd/system')/unit
  if p.exists() and not p.is_symlink():
   shutil.copy2(p,audit/unit);p.unlink()
 run(['systemctl','daemon-reload'])
 backup_ok()
 result={'cleaned_at':time.time(),'backup_preserved':True,'opt_remaining':[p.name for p in Path('/opt').iterdir()],'removed_packages':packages,'removed_containers':len(ids),'migration_services_started':False}
 (audit/'result.json').write_text(json.dumps(result,indent=2))
 public=Path('/home/varma/migration_2026_09_25/cleanup_result.json');public.write_text(json.dumps(result,indent=2));public.chmod(0o644)
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
