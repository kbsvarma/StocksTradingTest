"""Verify the archive after rsync's complete content-checksum pass."""
from pathlib import Path
import json,subprocess,time,os,stat
H=Path('/home/varma');C=H/'.data_migration_control_20260926';A=H/'old_server_archive_2026_09_26';D=A/'home/varmak'
def status(stage,**extra):
 (C/'postverify_status.json').write_text(json.dumps({'stage':stage,'time':time.time(),**extra},indent=2))
status('waiting_for_copy_and_checksums')
while not (C/'verification_result.json').exists():
 if (C/'transfer_result.json').exists() and json.loads((C/'transfer_result.json').read_text())['returncode']:
  status('blocked_copy_failed');raise SystemExit(1)
 time.sleep(20)
v=json.loads((C/'verification_result.json').read_text())
if v['returncode'] or v['difference_log_bytes']:
 status('blocked_checksum_differences',verification=v);raise SystemExit(1)
status('checking_destination_databases')
r=subprocess.run(['/usr/bin/python3',str(C/'check_sqlite.py'),str(D),str(A/'audit/final_inventory/summary.json'),str(A/'audit/sqlite_destination.json')],stdout=(C/'sqlite-check.log').open('w'),stderr=subprocess.STDOUT)
checks=json.loads((A/'audit/sqlite_destination.json').read_text())
if r.returncode or not checks['ok']:
 status('blocked_database_check',returncode=r.returncode);raise SystemExit(1)
status('checking_inventory')
errors=[];count=0;expected=set()
for l in (A/'audit/final_inventory/files.jsonl').open():
 rec=json.loads(l);rel=rec['path'];expected.add(rel)
 if rel=='.ssh/authorized_keys':continue # Copied separately after temporary transfer-key removal.
 p=D/rel
 try:
  st=p.lstat();kind='file' if stat.S_ISREG(st.st_mode) else 'dir' if stat.S_ISDIR(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'special'
  if kind!=rec['kind']:errors.append({'path':rel,'reason':'type'})
  elif kind=='file' and st.st_size!=rec['bytes']:errors.append({'path':rel,'reason':'size','expected':rec['bytes'],'actual':st.st_size})
  elif kind=='symlink' and os.readlink(p)!=rec['target']:errors.append({'path':rel,'reason':'symlink'})
  count+=1
 except OSError as e:errors.append({'path':rel,'reason':str(e)})
unexpected=[]
for parent,dirs,files in os.walk(D,followlinks=False):
 for name in dirs+files:
  rel=str((Path(parent)/name).relative_to(D))
  if rel not in expected:unexpected.append(rel)
if unexpected:errors.extend({'path':rel,'reason':'unexpected destination entry'} for rel in unexpected)
(A/'audit/inventory_comparison.json').write_text(json.dumps({'checked_entries':count,'errors':errors},indent=2))
if errors:
 status('blocked_inventory_differences',count=len(errors));raise SystemExit(1)
status('verified_awaiting_final_handover',checked_entries=count,sqlite_databases=checks['checked'])
