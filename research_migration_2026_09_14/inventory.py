from pathlib import Path
import os,json,time,subprocess,collections
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'research_migration_2026_09_14'
roots=[p for p in ROOT.iterdir() if p.is_dir() and '2026_' in p.name and not p.name.startswith('.') and p.name!=OUT.name and 'restart_audit' not in p.name]
roots += [ROOT/p for p in ['research_snapshots','shares_research','event_edge_research','trend_bot/research','spx_spread_bot/backtests','spx_spread_bot/data/databento'] if (ROOT/p).is_dir()]
skipdirs={'.git','.venv','venv','node_modules','__pycache__','.pytest_cache','logs','state','live','live_v1','live_v2'}
dataext={'.parquet','.zst','.dbn','.npz','.npy','.pkl','.feather','.arrow','.h5','.hdf5'}
rows=[];now=time.time();seen=set()
for root in roots:
 for base,dirs,files in os.walk(root,followlinks=False):
  dirs[:]=[d for d in dirs if d not in skipdirs and not d.startswith('.')]
  for name in files:
   f=Path(base)/name;rel=f.relative_to(ROOT).as_posix()
   if rel in seen or f.is_symlink():continue
   seen.add(rel)
   if name.startswith('.') or f.suffix in {'.log','.db','.sqlite','.sqlite3','.pem','.key','.pyc'} or any(w in name.lower() for w in ['credential','secret','token','password']):continue
   st=f.stat();move=f.suffix.lower() in dataext or (f.suffix.lower() in {'.csv','.json','.jsonl','.gz','.zip'} and st.st_size>=1024**2)
   if now-st.st_mtime<300:move=False
   rows.append(dict(path=rel,size=st.st_size,mtime_ns=st.st_mtime_ns,inode=st.st_ino,move=move))
rows.sort(key=lambda r:(not r['move'],-r['size'],r['path']))
(OUT/'inventory.json').write_text(json.dumps(rows))
tot=collections.defaultdict(int)
for r in rows:
 if r['move']:tot[r['path'].split('/')[0]]+=r['size']
print(json.dumps(dict(files=len(rows),move_files=sum(r['move'] for r in rows),move_GiB=sum(r['size'] for r in rows if r['move'])/1024**3,copy_GiB=sum(r['size'] for r in rows)/1024**3,top_GiB={k:round(v/1024**3,2) for k,v in sorted(tot.items(),key=lambda kv:-kv[1])[:20]}),indent=2))
