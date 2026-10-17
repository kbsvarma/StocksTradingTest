from pathlib import Path
import hashlib,json,os,subprocess,time,collections
R=Path(__file__).resolve().parents[2];P=Path(__file__).resolve().parent;os.nice(15)
(P/'git_refs_before.txt').write_bytes(subprocess.check_output(['git','show-ref'],cwd=R));(P/'git_worktrees_before.txt').write_bytes(subprocess.check_output(['git','worktree','list','--porcelain'],cwd=R))
proc=subprocess.run(['lsof','-nP','-Fn'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);assert proc.returncode==0;opened={x[1:] for x in proc.stdout.splitlines() if x.startswith('n')};rows=[]
for f in sorted((R/'.git/objects/pack').glob('tmp_pack_*')):
 st=f.stat()
 if str(f) in opened:print('SKIP active',f.name,flush=True);continue
 h=hashlib.sha256()
 with f.open('rb') as stream:
  for b in iter(lambda:stream.read(16*1024**2),b''):h.update(b)
 end=f.stat();assert (st.st_size,st.st_mtime_ns,st.st_ino)==(end.st_size,end.st_mtime_ns,end.st_ino)
 row=dict(path=str(f),name=f.name,size=st.st_size,mtime_ns=st.st_mtime_ns,inode=st.st_ino,sha256=h.hexdigest());rows.append(row);(P/'git_temporary_packs.json').write_text(json.dumps(rows,indent=2));print('HASHED',f.name,round(st.st_size/1024**3,3),'GiB',h.hexdigest()[:12],flush=True)
unique={r['sha256']:r['size'] for r in rows};print('TOTAL',len(rows),'files',sum(r['size'] for r in rows)/1024**3,'GiB; UNIQUE',len(unique),sum(unique.values())/1024**3,'GiB',flush=True);(P/'GIT_HASHES_COMPLETE').write_text(str(time.time()))
