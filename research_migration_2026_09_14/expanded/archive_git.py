from pathlib import Path
import subprocess,shlex,json,os,hashlib,time
R=Path(__file__).resolve().parents[2];P=Path(__file__).resolve().parent;HOST='varmak@192.168.3.36';DEST='/home/varmak/research_data/_git_archives/StocksTradingTest/2026-09-14';os.nice(15)
assert (P/'GIT_HASHES_COMPLETE').exists();rows=json.loads((P/'git_temporary_packs.json').read_text());groups={}
for r in rows:groups.setdefault(r['sha256'],[]).append(r)
subprocess.run(['ssh','-o','BatchMode=yes',HOST,'mkdir -p '+DEST+'/objects && chmod 700 /home/varmak/research_data/_git_archives'],check=True)
subprocess.run(['scp','-q',str(P/'git_temporary_packs.json'),str(P/'git_refs_before.txt'),str(P/'git_worktrees_before.txt'),HOST+':'+DEST+'/'],check=True)
removed=[];archived=[]
for i,(digest,items) in enumerate(groups.items(),1):
 source=Path(items[0]['path']);target=DEST+'/objects/'+digest+'.pack-fragment'
 print('ARCHIVE',i,'/',len(groups),round(items[0]['size']/1024**3,3),'GiB;',len(items),'local copies',flush=True)
 subprocess.run(['rsync','-a','--partial','--bwlimit=60000','--rsync-path=ionice -c2 -n7 nice -n 15 rsync','-e','ssh -o BatchMode=yes',str(source),HOST+':'+target],check=True)
 code="import hashlib,pathlib; p=pathlib.Path("+repr(target)+");h=hashlib.sha256();f=p.open('rb');[(h.update(b)) for b in iter(lambda:f.read(16*1024**2),b'')];print(h.hexdigest(),p.stat().st_size)"
 answer=subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'ionice -c2 -n7 nice -n 15 python3 -c '+shlex.quote(code)],text=True).split();assert answer==[digest,str(items[0]['size'])],answer
 archived.append(dict(sha256=digest,size=items[0]['size'],remote=target))
 proc=subprocess.run(['lsof','-nP','-Fn'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);assert proc.returncode==0;opened={s[1:] for s in proc.stdout.splitlines() if s.startswith('n')}
 count=subprocess.run(['git','count-objects','-v'],cwd=R,capture_output=True,text=True);warnings=count.stderr
 for r in items:
  f=Path(r['path']);st=f.stat()
  assert 'garbage found: '+str(f.relative_to(R)) in warnings
  if str(f) in opened or (st.st_size,st.st_mtime_ns,st.st_ino)!=(r['size'],r['mtime_ns'],r['inode']):print('RETAIN changed/open',f.name,flush=True);continue
  f.unlink();removed.append(r)
 (P/'git_archived.json').write_text(json.dumps(archived,indent=2));(P/'git_removed.json').write_text(json.dumps(removed,indent=2));print('REMOVED cumulative',round(sum(r['size'] for r in removed)/1024**3,3),'GiB',flush=True)
# Finder metadata is not a valid Git reference; archive it to resolve the pre-existing fsck error.
f=R/'.git/refs/.DS_Store'
if f.exists():
 data=f.read_bytes();digest=hashlib.sha256(data).hexdigest();target=DEST+'/refs.DS_Store';subprocess.run(['scp','-q',str(f),HOST+':'+target],check=True)
 actual=subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'sha256sum '+target],text=True).split()[0];assert actual==digest
 if f.read_bytes()==data:f.unlink()
 (P/'finder_metadata_receipt.json').write_text(json.dumps(dict(original=str(f),remote=target,sha256=digest)))
fsck=subprocess.run(['git','fsck','--connectivity-only','--no-dangling'],cwd=R,capture_output=True,text=True);(P/'git_fsck_after.log').write_text(fsck.stdout+fsck.stderr);assert fsck.returncode==0,fsck.stderr
refs=subprocess.check_output(['git','show-ref'],cwd=R);worktrees=subprocess.check_output(['git','worktree','list','--porcelain'],cwd=R);assert refs==(P/'git_refs_before.txt').read_bytes();assert worktrees==(P/'git_worktrees_before.txt').read_bytes()
summary=dict(removed_files=len(removed),removed_bytes=sum(r['size'] for r in removed),unique_archive_bytes=sum(r['size'] for r in archived),git_connectivity='passed',refs_and_worktrees='unchanged');(P/'git_cleanup_summary.json').write_text(json.dumps(summary,indent=2))
subprocess.run(['scp','-q',str(P/'git_archived.json'),str(P/'git_removed.json'),str(P/'git_cleanup_summary.json'),HOST+':'+DEST+'/'],check=True);(P/'GIT_CLEANUP_COMPLETE').write_text(str(time.time()));print('GIT CLEANUP COMPLETE',json.dumps(summary),flush=True)
