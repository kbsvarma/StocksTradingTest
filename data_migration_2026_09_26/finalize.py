"""Make verified historical data discoverable without changing live bot paths."""
from pathlib import Path
import os,json,time
H=Path('/home/varma');C=H/'.data_migration_control_20260926';A=H/'old_server_archive_2026_09_26';D=A/'home/varmak'
s=json.loads((C/'postverify_status.json').read_text())
if s['stage']!='verified_awaiting_final_handover':raise SystemExit('Archive has not passed all verification steps.')
if not (A/'audit/authorized_keys_verified.json').exists():raise SystemExit('Temporary transfer key cleanup and separate authorized_keys verification required.')
changes=[]
for l in (A/'audit/final_inventory/files.jsonl').open():
 r=json.loads(l)
 if r['kind']!='symlink':continue
 target=r['target'];old='/home/varmak'
 if target==old or target.startswith(old+'/'):
  p=D/r['path'];replacement=os.path.relpath(D/target[len(old):].lstrip('/'),p.parent)
  if os.readlink(p)==target:
   p.unlink();p.symlink_to(replacement)
  elif os.readlink(p)!=replacement:raise RuntimeError('Unexpected link change: '+r['path'])
  changes.append({'path':r['path'],'source_target':target,'destination_target':replacement})
(A/'audit/relocated_symlinks.json').write_text(json.dumps(changes,indent=2))
aliases={'research_data':D/'research_data','pm-history':D/'pm-history','legacy_kalshi':D/'kalshi','source_backups':D/'backups','research_models':D/'.local/share/advisor','old_server_archive':A}
created={}
for name,target in aliases.items():
 if not target.exists():continue
 p=H/name
 if p.exists() or p.is_symlink():
  if not p.is_symlink() or p.resolve()!=target.resolve():raise RuntimeError('Existing destination path must not be overwritten: '+str(p))
 else:p.symlink_to(os.path.relpath(target,H))
 created[str(p)]=str(target)
(A/'audit/data_locations.json').write_text(json.dumps(created,indent=2))
summary=json.loads((A/'audit/final_inventory/summary.json').read_text())
readme='''# Old server data archive

Verified copy of the trading/research account from varmak@192.168.3.36.
The three production services run separately under /home/varma and were not overwritten.

## Data locations

'''
for name,target in created.items():readme+=f'- `{name}` -> `{target}`\n'
readme+='''
## Verification

The complete account copy passed rsync's file-content checksum comparison, the recursive inventory comparison, and SQLite integrity checks. Audit receipts and original file metadata are in `audit/`. Authorized keys were copied separately after removing the temporary read-only transfer key. Absolute symlinks within the old home were then converted to equivalent relative links inside this archive; `audit/relocated_symlinks.json` records each intentional change. File contents were not rewritten for path relocation.

Historical environments and scripts are preserved as evidence, not enabled as services. Their embedded absolute paths may require project-specific environment rebuilding before re-execution. Legacy trading strategies and old collectors remain stopped. Use the existing production Trend, Kalshi crypto and Advisor installations for live operation.

Other users' accounts, unrelated system applications, and operating-system partitions are not part of this trading/research account archive. The protected backup mount on the new server was not modified. Source files were not deleted.
'''
(A/'README.md').write_text(readme)
(C/'handover_result.json').write_text(json.dumps({'time':time.time(),'status':'complete','files_in_source_inventory':summary['counts']['file'],'sqlite_databases':len(summary['sqlite_databases']),'relocated_symlinks':len(changes),'data_locations':created},indent=2))
print(json.dumps({'status':'complete','relocated_symlinks':len(changes),'aliases':list(created)}))
