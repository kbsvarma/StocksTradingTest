from pathlib import Path
import json,subprocess,os,hashlib,time,shlex,fcntl,sys
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'research_migration_2026_09_14';HOST='varmak@192.168.3.36';REMOTE='/home/varmak/research_data/StocksTradingTest'
lock=open(OUT/'cleanup.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert (OUT/'COPY_COMPLETE').exists() or '--partial' in sys.argv
response=subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'cat '+REMOTE+'/research_migration_2026_09_14/REMOTE_SMOKE_PASSED.json'])
smoke=json.loads(response);assert smoke['status']=='passed' and smoke['independent_portfolio_checks']==20
(OUT/'REMOTE_SMOKE_PASSED.json').write_bytes(response)
raw=(OUT/'verified.jsonl').read_text();raw=raw[:raw.rfind('\n')+1];rows=[json.loads(s) for s in raw.splitlines()];selected=[r for r in rows if r['move']]
# Check remote copies still have their verified metadata. A full SHA256 check was already performed immediately after every transfer.
checkcode="""import sys,json,pathlib
p=pathlib.Path('/home/varmak/research_data/StocksTradingTest');result=[]
for r in json.load(sys.stdin):
 f=p/r['path']
 if not f.is_file() or f.stat().st_size!=r['size']:result.append(r['path'])
print(json.dumps(result))"""
bad=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'python3 -c '+shlex.quote(checkcode)],input=json.dumps(selected).encode()));assert not bad,('remote data changed',bad[:5])
# Never unlink datasets which a local process currently has open.
proc=subprocess.run(['lsof','-nP','-Fn'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
assert proc.returncode==0,'cannot inventory open local files'
opened={s[1:] for s in proc.stdout.splitlines() if s.startswith('n'+str(ROOT)+'/')}
removed=0;freed=0;skipped=[]
with (OUT/'removed.jsonl').open('a') as log:
 for r in selected:
  p=ROOT/r['path']
  if not p.exists():continue
  st=p.lstat()
  if p.is_symlink() or (st.st_size,st.st_mtime_ns,st.st_ino)!=(r['size'],r['mtime_ns'],r['inode']) or str(p) in opened:
   skipped.append(dict(path=r['path'],reason='changed_or_open_since_verified_copy'));continue
  # Atomic source identity recheck immediately before unlink; content hash was captured from this same unchanged inode/version.
  p.unlink();removed+=1;freed+=r['size'];log.write(json.dumps(dict(path=r['path'],size=r['size'],sha256=r['sha256'],removed_at=time.time()))+'\n')
  if removed%2000==0:log.flush();os.fsync(log.fileno());print('REMOVED',removed,'files;',round(freed/1024**3,3),'GiB',flush=True)
 log.flush();os.fsync(log.fileno())
(OUT/'cleanup_skipped.json').write_text(json.dumps(skipped,indent=2));all_removed=[json.loads(s) for s in (OUT/'removed.jsonl').read_text().splitlines()];total_removed=sum(r['size'] for r in all_removed);summary=dict(removed_files=len(all_removed),removed_bytes=total_removed,removed_GiB=total_removed/1024**3,skipped_files=len(skipped),remote=HOST+':'+REMOTE,cleanup_phase='partial' if '--partial' in sys.argv else 'complete')
(OUT/'cleanup_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
