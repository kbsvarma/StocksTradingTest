from pathlib import Path
import json,hashlib,csv,gzip,zipfile,os,shutil
import pyarrow.parquet as pq
BASE=Path('/home/varmak/research_data');M=BASE/'_migration_audits/2026-09-14-expanded';rows=[json.loads(s) for s in (M/'projects_verified.jsonl').read_text().splitlines()]
changes=[];samples={}
for r in rows:
 f=BASE/r['path'];project=r.get('project',r['path'].split('/')[0])
 if not r['move'] and f.suffix in {'.py','.sh','.toml','.yaml','.yml','.md','.txt','.csv','.json'} and r['size']<4*1024**2:
  try:text=f.read_text()
  except UnicodeError:continue
  old='/Users/varmakammili/Documents/GitHub/'
  if old in text:
   backup=M/'original_support'/r['path'];backup.parent.mkdir(parents=True,exist_ok=True)
   if not backup.exists():shutil.copy2(f,backup)
   assert hashlib.sha256(backup.read_bytes()).hexdigest()==r['sha256']
   updated=text.replace(old,str(BASE)+'/');temp=f.with_name(f.name+'.migration-tmp');temp.write_text(updated);shutil.copymode(f,temp);os.replace(temp,f);changes.append(r['path'])
 if r['move'] and project not in samples and r['size']>0:
  ext=f.suffix.lower()
  try:
   if ext=='.parquet':detail=dict(rows=pq.ParquetFile(f).metadata.num_rows)
   elif ext=='.json':
    with f.open() as s:obj=json.load(s)
    detail=dict(json_type=type(obj).__name__)
   elif ext in {'.jsonl','.ndjson'}:
    with f.open() as s:obj=json.loads(next(line for line in s if line.strip()))
    detail=dict(json_line_type=type(obj).__name__)
   elif ext in {'.csv','.tsv'}:
    with f.open() as s:first=next(csv.reader(s,delimiter='\t' if ext=='.tsv' else ','))
    detail=dict(columns=len(first))
   elif ext=='.zip':
    with zipfile.ZipFile(f) as z:detail=dict(zip_members=len(z.namelist()));assert z.testzip() is None
   else:
    with f.open('rb') as s:detail=dict(sample_bytes=len(s.read(512)))
   samples[project]=dict(path=r['path'],format=ext,detail=detail)
  except (ValueError,UnicodeError,StopIteration):continue
required={r.get('project',r['path'].split('/')[0]) for r in rows if r['move']};assert required<=set(samples),(required,set(samples))
result=dict(status='passed',projects_with_moved_data=sorted(required),sample_checks=samples,path_adjusted_support_files=len(changes));(M/'REMOTE_PROJECT_CHECKS_PASSED.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
