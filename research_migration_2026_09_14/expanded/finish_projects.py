from pathlib import Path
import subprocess,sys,json,shlex,time,os,collections,fcntl
P=Path(__file__).resolve().parent;BASE=Path('/Users/varmakammili/Documents/GitHub');HOST='varmak@192.168.3.36';REMOTE='/home/varmak/research_data';AUDIT=REMOTE+'/_migration_audits/2026-09-14-expanded'
while not (P/'PROJECTS_COPY_COMPLETE').exists():time.sleep(10)
print('Projects copied; uploading receipts and checking remote datasets.',flush=True)
subprocess.run(['ssh','-o','BatchMode=yes',HOST,'mkdir -p '+AUDIT+' && chmod 700 '+REMOTE+'/_migration_audits'],check=True)
files=[P/n for n in ['all_projects_inventory.json','project_inventory_summary.json','projects_verified.jsonl','remote_project_check.py']]
subprocess.run(['scp','-q',*[str(f) for f in files],HOST+':'+AUDIT+'/'],check=True)
subprocess.run(['ssh','-o','BatchMode=yes',HOST,REMOTE+'/research-run StocksTradingTest '+AUDIT+'/remote_project_check.py'],check=True)
passed=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'cat '+AUDIT+'/REMOTE_PROJECT_CHECKS_PASSED.json']));assert passed['status']=='passed';(P/'REMOTE_PROJECT_CHECKS_PASSED.json').write_text(json.dumps(passed,indent=2))
rows=[json.loads(s) for s in (P/'projects_verified.jsonl').read_text().splitlines()];selected=[r for r in rows if r['move']]
code="import json,sys,pathlib;p=pathlib.Path('/home/varmak/research_data');r=json.load(sys.stdin);bad=[x['path'] for x in r if not (p/x['path']).is_file() or (p/x['path']).stat().st_size!=x['size']];print(json.dumps(bad))"
bad=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,'python3 -c '+shlex.quote(code)],input=json.dumps(selected).encode()));assert not bad,bad[:5]
proc=subprocess.run(['lsof','-nP','-Fn'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);assert proc.returncode==0;opened={s[1:] for s in proc.stdout.splitlines() if s.startswith('n')};removed=[];retained=[]
with (P/'projects_removed.jsonl').open('a') as out:
 for r in selected:
  f=BASE/r['path']
  if not f.exists():continue
  st=f.lstat()
  if f.is_symlink() or str(f) in opened or (st.st_size,st.st_mtime_ns,st.st_ino)!=(r['size'],r['mtime_ns'],r['inode']):retained.append(r['path']);continue
  f.unlink();removed.append(r);out.write(json.dumps(r)+'\n')
  if len(removed)%20000==0:out.flush();os.fsync(out.fileno());print('REMOVED',len(removed),'files',round(sum(r['size'] for r in removed)/1024**3,3),'GiB',flush=True)
 out.flush();os.fsync(out.fileno())
summary=dict(removed_files=len(removed),removed_bytes=sum(r['size'] for r in removed),retained_changed_or_open=len(retained),projects={})
for project in sorted({r['project'] for r in rows}):
 z=[r for r in removed if r['project']==project];summary['projects'][project]=dict(removed_files=len(z),removed_bytes=sum(r['size'] for r in z))
 f=BASE/project/'REMOTE_RESEARCH_DATA.md';f.write_text('# Remote research data\n\nHistorical research data are at `'+HOST+':'+REMOTE+'/'+project+'`. '+str(len(z))+' verified research files from this project were removed locally to free space. Code, small reports, tracked fixtures and runtime databases remain local.\n\nRun research with `ssh '+HOST+' '+REMOTE+'/research-run '+project+' relative/script.py`. The shared Python runtime is isolated from bot deployments and limits each job to two CPU cores and 12 GiB memory. Scripts may need additional project-specific dependencies.\n\nPer-file SHA256 and restore paths are in the remote `'+AUDIT+'/projects_removed.jsonl`. The same audit files are local under `StocksTradingTest/research_migration_2026_09_14/expanded/`. For a removed relative file, copy it from `'+HOST+':'+REMOTE+'/'+project+'/RELATIVE_PATH` into this project and verify the receipt SHA256. Do not repopulate the entire cache on a nearly full machine.\n')
 subprocess.run(['scp','-q',str(f),HOST+':'+REMOTE+'/'+project+'/REMOTE_RESEARCH_DATA.md'],check=True)
(P/'projects_cleanup_summary.json').write_text(json.dumps(summary,indent=2));(P/'projects_retained_changed_or_open.json').write_text(json.dumps(retained,indent=2))
subprocess.run(['scp','-q',str(P/'projects_removed.jsonl'),str(P/'projects_cleanup_summary.json'),str(P/'projects_retained_changed_or_open.json'),HOST+':'+AUDIT+'/'],check=True)
(P/'PROJECTS_MIGRATION_COMPLETE').write_text(str(time.time()));print('PROJECTS MIGRATION COMPLETE',json.dumps(summary),flush=True)
