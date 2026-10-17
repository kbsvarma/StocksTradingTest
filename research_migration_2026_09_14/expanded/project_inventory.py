from pathlib import Path
import os,json,time,collections,subprocess
BASE=Path('/Users/varmakammili/Documents/GitHub');OUT=Path(__file__).resolve().parent
skipdirs={'.git','.venv','venv','node_modules','__pycache__','.pytest_cache','.next','build','builds','dist','target','Pods','.codex'}
research_terms=['research','backtest','back_test','snapshot','bhavcopy','historical','experiment','replay','databento']
binary={'.parquet','.zst','.dbn','.npy','.npz','.pkl','.feather','.arrow','.h5','.hdf5'}
textdata={'.csv','.json','.jsonl','.ndjson','.tsv','.gz','.zip','.log'}
rows=[];projects=[];now=time.time();tracked_cache={}
old_manifest=OUT.parent/'verified.jsonl';already_copied={json.loads(s)['path'] for s in old_manifest.read_text().splitlines()}
for project in sorted(BASE.iterdir()):
 if not project.is_dir():continue
 tracked=set(subprocess.run(['git','-C',str(project),'ls-files','-z'],capture_output=True).stdout.decode(errors='replace').split('\0'));counts=collections.Counter();bytecounts=collections.Counter()
 for base,dirs,files in os.walk(project,followlinks=False):
  dirs[:]=[d for d in dirs if d not in skipdirs and not d.startswith('.') or (d=='.claude' and project.name=='StocksTradingTest')]
  relbase=Path(base).relative_to(project)
  if any(p.startswith('research_migration_2026_09_14') for p in relbase.parts):dirs[:]=[];continue
  # Linked worktrees are inventoried independently; copy their code/data, never their Git pointers.
  localroot=project
  if '.claude' in relbase.parts and 'worktrees' in relbase.parts:
   ix=relbase.parts.index('worktrees')
   if len(relbase.parts)>ix+1:localroot=project.joinpath(*relbase.parts[:ix+2])
  for name in files:
   f=Path(base)/name
   if name.startswith('.') or f.is_symlink() or f.suffix.lower() in {'.env','.pem','.key','.p12','.pfx','.pyc'} or any(w in name.lower() for w in ['credential','secret','password','token']):continue
   try:st=f.stat()
   except FileNotFoundError:continue
   rel=f.relative_to(project).as_posix();parts=f.relative_to(project).parts;low='/'.join(parts).lower();ext=f.suffix.lower()
   is_research=any(t in low for t in research_terms) or 'data' in parts or ('logs' in parts and project.name in ['PolyMarketTestBot','mlb-arb-bot','KalshiWeatherTest'])
   snapshot=any(t in low for t in ['snapshot','backup','archive'])
   runtime=any(p in {'state','live','live_v1','live_v2','webull_live'} for p in parts) and not snapshot
   database=ext in {'.sqlite','.sqlite3','.db'} or name.endswith(('-wal','-shm'))
   fixture=any(p in {'tests','fixtures','testdata','examples','sample_data'} for p in parts)
   data=is_research and (ext in binary|textdata or (snapshot and database)) and not runtime and not fixture
   # Keep small reports, summaries, configuration and ledgers local; raw data can be tiny per file.
   raw_context=any(p in {'data','raw','cache','historical','snapshots','logs'} for p in parts) or snapshot
   reportlike=not raw_context and ext in {'.csv','.json','.tsv'} and st.st_size<1024**2
   move=data and not reportlike and now-st.st_mtime>86400 and rel not in tracked
   if project.name=='StocksTradingTest' and rel.startswith(('cover_','stock_overnight_','databento_fresh_')) and not raw_context:move=False
   if localroot!=project and move:
    if str(localroot) not in tracked_cache:tracked_cache[str(localroot)]=set(subprocess.run(['git','-C',str(localroot),'ls-files','-z'],capture_output=True).stdout.decode(errors='replace').split('\0'))
    if str(f.relative_to(localroot)) in tracked_cache[str(localroot)]:move=False
   if database and not snapshot:counts['runtime_database_retained']+=1;bytecounts['runtime_database_retained']+=st.st_size;continue
   support=ext in {'.py','.md','.toml','.yaml','.yml','.txt','.csv','.json'} and st.st_size<4*1024**2 and not runtime
   if not (data or support):counts['nonresearch_or_runtime_retained']+=1;bytecounts['nonresearch_or_runtime_retained']+=st.st_size;continue
   if project.name=='StocksTradingTest' and rel in already_copied and not move:continue
   category='research_move' if move else 'support_or_tracked_copy';counts[category]+=1;bytecounts[category]+=st.st_size
   rows.append(dict(path=project.name+'/'+rel,size=st.st_size,mtime_ns=st.st_mtime_ns,inode=st.st_ino,move=move,project=project.name,category=category))
 projects.append(dict(project=project.name,counts=dict(counts),bytes=dict(bytecounts)));print(project.name,dict(counts),{k:round(v/1024**3,3) for k,v in bytecounts.items()},flush=True)
rows.sort(key=lambda x:(not x['move'],-x['size'],x['path']));(OUT/'all_projects_inventory.json').write_text(json.dumps(rows));(OUT/'project_inventory_summary.json').write_text(json.dumps(projects,indent=2));print('TOTAL',len(rows),'COPY GiB',sum(r['size'] for r in rows)/1024**3,'MOVE GiB',sum(r['size'] for r in rows if r['move'])/1024**3,flush=True)
