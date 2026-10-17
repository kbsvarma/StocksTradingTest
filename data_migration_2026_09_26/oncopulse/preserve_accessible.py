"""Preserve accessible OncoPulse database/configuration pending privileged code export."""
import subprocess,json,shlex,hashlib
from pathlib import Path
old=['ssh','-o','BatchMode=yes','varmak@192.168.3.36']
new=['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','varma@192.168.3.136']
code='''from pathlib import Path
import sqlite3,contextlib
p=Path('/home/varmak/.data_migration_control_20260926/oncopulse_initial.sqlite')
if p.exists():raise SystemExit('Snapshot already exists; refusing overwrite.')
with contextlib.closing(sqlite3.connect('file:/var/lib/oncopulse/oncopulse.db?mode=ro',uri=True)) as src:
 with contextlib.closing(sqlite3.connect(str(p))) as dst:
  src.backup(dst)
  assert dst.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
p.chmod(0o600)
'''
subprocess.run(old+['python3 -c '+shlex.quote(code)],check=True)
base='/home/varma/old_server_archive_2026_09_26/additional_projects/oncopulse/initial_snapshot'
inputs={'oncopulse.db':'/home/varmak/.data_migration_control_20260926/oncopulse_initial.sqlite','database_baseline.json':'/home/varmak/.data_migration_control_20260926/oncopulse_database_baseline.json','oncopulse.service':'/etc/systemd/system/oncopulse.service','nginx-streamlit.conf':'/etc/nginx/sites-available/streamlit'}
receipts=[]
for name,source in inputs.items():
 raw=subprocess.check_output(old+['cat '+shlex.quote(source)]);digest=hashlib.sha256(raw).hexdigest()
 code=f'''from pathlib import Path
import sys,hashlib
p=Path({base!r});p.mkdir(parents=True,mode=0o700,exist_ok=True);p.chmod(0o700)
f=p/{name!r};raw=sys.stdin.buffer.read()
if f.exists():assert f.read_bytes()==raw,'Existing snapshot differs'
else:
 with f.open('xb') as o:o.write(raw)
f.chmod(0o600)
assert hashlib.sha256(f.read_bytes()).hexdigest()=={digest!r}
'''
 subprocess.run(new+['python3 -c '+shlex.quote(code)],input=raw,check=True)
 receipts.append({'source':source,'destination':base+'/'+name,'bytes':len(raw),'sha256':digest})
code=f'''import sqlite3,json,contextlib
with contextlib.closing(sqlite3.connect('file:'+{base+'/oncopulse.db'!r}+'?mode=ro',uri=True)) as c:
 assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
 print(json.dumps({{'integrity':'ok','items':c.execute('select count(*) from items').fetchone()[0],'runs':c.execute('select count(*) from run_history').fetchone()[0]}}))
'''
result=json.loads(subprocess.check_output(new+['python3 -c '+shlex.quote(code)],text=True))
report={'status':'database_and_configuration_preserved; protected_deployment_pending','destination_checks':result,'files':receipts}
Path(__file__).with_name('initial_snapshot_receipt.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'status':report['status'],'destination_checks':result,'files':len(receipts)}))
