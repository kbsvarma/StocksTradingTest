from pathlib import Path
import subprocess,json,hashlib,os,time,shlex,fcntl
ROOT=Path('/Users/varmakammili/Documents/GitHub');OUT=Path(__file__).resolve().parent;HOST='varmak@192.168.3.36';DEST='/home/varmak/research_data'
lock=open(OUT/'projects_transfer.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
os.nice(15)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
 return h.hexdigest()
remote_code='''import sys,json,hashlib,pathlib
root=pathlib.Path('/home/varmak/research_data');out=[]
for r in json.load(sys.stdin):
 p=root/r['path'];h=hashlib.sha256()
 try:
  with p.open('rb') as f:
   for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
  out.append({'path':r['path'],'sha256':h.hexdigest(),'size':p.stat().st_size})
 except Exception as e:out.append({'path':r['path'],'error':str(e)})
print(json.dumps(out))'''
rows=json.loads((OUT/'all_projects_inventory.json').read_text());done={}
if (OUT/'projects_verified.jsonl').exists():
 for line in (OUT/'projects_verified.jsonl').read_text().splitlines():
  r=json.loads(line);done[r['path']]=r
pending=[r for r in rows if r['path'] not in done];groups=[];g=[];size=0
for r in pending:
 if g and (size+r['size']>2*1024**3 or len(g)>=4000):groups.append(g);g=[];size=0
 g.append(r);size+=r['size']
if g:groups.append(g)
for i,g in enumerate(groups,1):
 prepared=[]
 for r in g:
  f=ROOT/r['path'];st=f.stat();digest=sha(f);after=f.stat()
  if (st.st_size,st.st_mtime_ns,st.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
   with (OUT/'projects_skipped.jsonl').open('a') as out:out.write(json.dumps(dict(path=r['path'],reason='changed_during_source_hash'))+'\n')
   continue
  prepared.append(dict(r,size=st.st_size,mtime_ns=st.st_mtime_ns,inode=st.st_ino,sha256=digest))
 if not prepared:continue
 print(f'BATCH {i}/{len(groups)} copy {len(prepared)} files {sum(r["size"] for r in prepared)/1024**3:.3f} GiB',flush=True)
 command=['rsync','-aR','--partial','--ignore-existing','--bwlimit=40000','--from0','--files-from=-','--rsync-path=ionice -c2 -n7 nice -n 15 rsync','-e','ssh -o BatchMode=yes -o ConnectTimeout=15',str(ROOT)+'/',HOST+':'+DEST+'/']
 result=subprocess.run(command,input=b''.join(r['path'].encode()+b'\0' for r in prepared),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if result.returncode:raise RuntimeError('rsync failed: '+result.stderr.decode()[-3000:])
 response=subprocess.run(['ssh','-o','BatchMode=yes',HOST,'ionice -c2 -n7 nice -n 15 python3 -c '+shlex.quote(remote_code)],input=json.dumps(prepared).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
 verified={r['path']:r for r in json.loads(response.stdout)};accepted=[]
 for r in prepared:
  v=verified[r['path']]
  if v.get('sha256')!=r['sha256'] or v.get('size')!=r['size']:
   with (OUT/'projects_skipped.jsonl').open('a') as out:out.write(json.dumps(dict(path=r['path'],reason='remote_hash_mismatch_local_retained'))+'\n')
   continue
  r['verified_at']=time.time();accepted.append(r)
 with (OUT/'projects_verified.jsonl').open('a') as out:
  for r in accepted:out.write(json.dumps(r)+'\n')
  out.flush();os.fsync(out.fileno())
 done.update({r['path']:r for r in accepted});total=sum(r['size'] for r in done.values())
 print(f'VERIFIED {len(done)} files / {total/1024**3:.3f} GiB; local copies retained pending smoke test',flush=True)
 (OUT/'projects_progress.json').write_text(json.dumps(dict(verified_files=len(done),verified_bytes=total,total_files=len(rows),batch=i,batches=len(groups),status='copying'),indent=2))
(OUT/'PROJECTS_COPY_COMPLETE').write_text(str(time.time()));print('COPY COMPLETE',flush=True)
