from pathlib import Path
import json,shutil,hashlib,os
P=Path(__file__).resolve().parent.parent;M=P/'research_migration_2026_09_14';old='/Users/varmakammili/Documents/GitHub/StocksTradingTest';changes=[]
records=(M/'verified.jsonl').read_text()
if (M/'support_verified.jsonl').exists():records+=(M/'support_verified.jsonl').read_text()
for line in records.splitlines():
 r=json.loads(line);f=P/r['path']
 if r['move'] or f.suffix not in {'.py','.sh','.yaml','.yml','.toml','.json','.csv','.md','.txt'} or r['size']>4*1024**2:continue
 try:text=f.read_text()
 except UnicodeError:continue
 if old not in text:continue
 backup=M/'original_path_files'/r['path'];backup.parent.mkdir(parents=True,exist_ok=True)
 if not backup.exists():shutil.copy2(f,backup)
 assert hashlib.sha256(backup.read_bytes()).hexdigest()==r['sha256']
 updated=text.replace(old,str(P));temp=f.with_name(f.name+'.migration-tmp');temp.write_text(updated);shutil.copymode(f,temp);os.replace(temp,f)
 changes.append(dict(path=r['path'],original_sha256=r['sha256'],runtime_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),original_copy=str(backup.relative_to(P))))
(M/'path_adjustments.json').write_text(json.dumps(changes,indent=2));print('Adjusted legacy workspace paths in',len(changes),'support files; originals preserved; moved data untouched.')
# Adapt the shared Databento access module to Linux without storing a key remotely.
f=P/'cover_validation_2026_09/db_access.py'
if f.exists():
 text=f.read_text();old_line="KEY=subprocess.check_output(['security','find-generic-password','-a','StocksTradingTest','-s','com.stockstradingtest.databento','-w'],text=True).strip()"
 if old_line in text:
  rel=f.relative_to(P).as_posix();backup=M/'original_path_files'/rel;backup.parent.mkdir(parents=True,exist_ok=True)
  if not backup.exists():shutil.copy2(f,backup)
  replacement="import os\nKEY=os.environ.get('DATABENTO_API_KEY','').strip()\nif not KEY:raise RuntimeError('Forward the primary key with local run_remote.py --databento; no key is stored in this research workspace.')"
  updated=text.replace(old_line,replacement);compile(updated,str(f),'exec');temp=f.with_name(f.name+'.migration-tmp');temp.write_text(updated);shutil.copymode(f,temp);os.replace(temp,f)
  changes.append(dict(path=rel,change='Linux environment credential adapter',original_copy=str(backup.relative_to(P)),original_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),runtime_sha256=hashlib.sha256(f.read_bytes()).hexdigest()))
  (M/'path_adjustments.json').write_text(json.dumps(changes,indent=2));print('Linux Databento adapter ready; no key copied or API call made.')
