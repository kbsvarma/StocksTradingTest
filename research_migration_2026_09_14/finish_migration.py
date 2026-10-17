from pathlib import Path
import subprocess,time,json,sys,collections
ROOT=Path(__file__).resolve().parent.parent;P=ROOT/'research_migration_2026_09_14';HOST='varmak@192.168.3.36';DEST='/home/varmak/research_data/StocksTradingTest'
while not (P/'COPY_COMPLETE').exists():time.sleep(10)
print('Copy complete; preparing remote workspace.',flush=True)
files=[P/n for n in ['inventory.json','verified.jsonl','prepare_remote_paths.py','remote_smoke.py','requirements.txt','run_remote.py','restore_data.py']]
subprocess.run(['scp','-q',*[str(f) for f in files],HOST+':'+DEST+'/research_migration_2026_09_14/'],check=True)
subprocess.run(['scp','-q',str(ROOT/'RESEARCH_DATA_REMOTE.md'),HOST+':'+DEST+'/RESEARCH_DATA_REMOTE.md'],check=True)
subprocess.run(['ssh','-o','BatchMode=yes',HOST,DEST+'/research-python research_migration_2026_09_14/prepare_remote_paths.py'],check=True)
subprocess.run(['ssh','-o','BatchMode=yes',HOST,'test -f '+DEST+'/research_migration_2026_09_14/REMOTE_SMOKE_PASSED.json || '+DEST+'/research-python research_migration_2026_09_14/remote_smoke.py'],check=True)
print('Remote reproduction passed; removing verified unchanged local data.',flush=True)
subprocess.run([sys.executable,str(P/'remove_verified_local.py')],check=True)
removed=[json.loads(s) for s in (P/'removed.jsonl').read_text().splitlines()];counts=collections.Counter(r['path'].split('/')[0] for r in removed)
for top,count in counts.items():
 f=ROOT/top/'REMOTE_DATA_LOCATION.md'
 if not f.exists():f.write_text(f'# Remote research data\n\n{count:,} historical data files from this folder were checksum-verified on `varmak@192.168.3.36:{DEST}/{top}` and removed locally to free disk space. Code and small reports remain local.\n\nSee the repository root `RESEARCH_DATA_REMOTE.md` and `research_migration_2026_09_14/removed.jsonl` for run/restore commands and per-file SHA256 receipts.\n')
report=ROOT/'RESEARCH_DATA_REMOTE.md';t=report.read_text().replace('are being migrated','have been migrated');summary=json.loads((P/'cleanup_summary.json').read_text());t+='\n## Completed migration\n\n'+f'Removed {summary["removed_files"]:,} verified local data files ({summary["removed_GiB"]:.2f} GiB of logical file sizes). Retained {summary["skipped_files"]} changed/open files locally. Remote independent reproduction passed all 20 portfolio arithmetic checks and decoded the raw Databento data.\n';report.write_text(t)
subprocess.run(['scp','-q',str(P/'removed.jsonl'),str(P/'cleanup_summary.json'),str(P/'cleanup_skipped.json'),HOST+':'+DEST+'/research_migration_2026_09_14/'],check=True)
subprocess.run(['scp','-q',str(report),HOST+':'+DEST+'/RESEARCH_DATA_REMOTE.md'],check=True)
(P/'MIGRATION_COMPLETE').write_text(str(time.time()));print('MIGRATION COMPLETE',flush=True)
