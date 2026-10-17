"""Read-only recursive metadata inventory; does not follow symlinks."""
import os,stat,json,sys,time
from pathlib import Path
from collections import defaultdict
root=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
counts=defaultdict(int);groups=defaultdict(lambda:{'files':0,'bytes':0});errors=[];links=[];dbs=[]
with (out/'files.jsonl').open('w') as f:
 for parent,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:errors.append({'path':e.filename,'error':str(e)})):
  dirs[:]=[d for d in dirs if d!='.data_migration_control_20260926']
  for n in dirs+files:
   p=Path(parent)/n;rel=str(p.relative_to(root))
   try:
    s=p.lstat();kind='file' if stat.S_ISREG(s.st_mode) else 'dir' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'special'
    rec={'path':rel,'kind':kind,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'mode':stat.S_IMODE(s.st_mode)};counts[kind]+=1
    if kind=='file':
     g=rel.split('/')[0];groups[g]['files']+=1;groups[g]['bytes']+=s.st_size
     if p.suffix.lower() in ['.db','.sqlite','.sqlite3']:
      try:
       with p.open('rb') as q:
        if q.read(16)==b'SQLite format 3\x00':dbs.append(rel)
      except OSError as e:errors.append({'path':rel,'error':str(e)})
    elif kind=='symlink':
     rec['target']=os.readlink(p)
     if os.path.isabs(rec['target']):links.append({'path':rel,'target':rec['target']})
    f.write(json.dumps(rec)+'\n')
   except OSError as e:errors.append({'path':rel,'error':str(e)})
summary={'root':str(root),'time':time.time(),'counts':dict(counts),'groups':dict(groups),'errors':errors,'absolute_symlinks':links,'sqlite_databases':dbs}
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({'counts':dict(counts),'logical_GiB':round(sum(g['bytes'] for g in groups.values())/2**30,2),'errors':errors,'absolute_symlinks':len(links),'sqlite_databases':len(dbs)}))
