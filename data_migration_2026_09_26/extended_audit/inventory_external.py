"""Read file metadata only; write private inventory reports. No application changes."""
import os,stat,json,time,pwd,sys
from pathlib import Path
from collections import defaultdict
base=Path('/home/varmak/.data_migration_control_20260926/extended_audit')
base.mkdir(mode=0o700,parents=True,exist_ok=True)
role='root' if os.geteuid()==0 else 'user'
out=base/role;out.mkdir(mode=0o700,exist_ok=True)
roots=['/home','/root','/opt','/srv','/usr/local','/var','/etc','/tmp','/mnt','/media']
counts=defaultdict(int);groups=defaultdict(lambda:{'files':0,'bytes':0});errors=[];markers=[];links=[]
uid=pwd.getpwnam('varmak').pw_uid;gid=pwd.getpwnam('varmak').pw_gid
with (out/'files.jsonl').open('w') as f:
 for root in roots:
  for parent,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:errors.append({'path':e.filename,'error':str(e)})):
   if parent=='/home':dirs[:]=[d for d in dirs if d not in ['varmak','ashish']]
   for name in dirs+files:
    p=Path(parent)/name
    try:
     s=p.lstat();kind='file' if stat.S_ISREG(s.st_mode) else 'dir' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'special'
     rec={'path':str(p),'kind':kind,'bytes':s.st_size,'uid':s.st_uid,'gid':s.st_gid,'mode':stat.S_IMODE(s.st_mode),'mtime_ns':s.st_mtime_ns};counts[kind]+=1
     if kind=='symlink':rec['target']=os.readlink(p)
     if name in ['.git','pyproject.toml','package.json','docker-compose.yml','compose.yaml','Cargo.toml','requirements.txt']:markers.append(str(p))
     if kind=='file':
      group='/'.join(p.parts[:4]);groups[group]['files']+=1;groups[group]['bytes']+=s.st_size
     f.write(json.dumps(rec)+'\n')
    except OSError as e:errors.append({'path':str(p),'error':str(e)})
summary={'time':time.time(),'effective_uid':os.geteuid(),'roots':roots,'excluded':['/home/varmak (already fully migrated)','/home/ashish (user explicitly excluded)'],'counts':dict(counts),'groups':dict(groups),'errors':errors,'project_markers':markers}
(out/'summary.json').write_text(json.dumps(summary,indent=2))
for p in [base,out,out/'files.jsonl',out/'summary.json']:
 if os.geteuid()==0:os.chown(p,uid,gid)
print(json.dumps({'report':str(out),'counts':dict(counts),'inaccessible_paths':len(errors),'project_markers':len(markers)}))
