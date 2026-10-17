"""Switch current research pointers only after destination handover succeeds."""
from pathlib import Path
import json, shutil, subprocess
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'data_migration_2026_09_26'
identity=Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'
ssh=['ssh','-i',str(identity),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','varma@192.168.3.136']
r=json.loads(subprocess.check_output(ssh+['cat /home/varma/.data_migration_control_20260926/handover_result.json'],text=True))
assert r['status']=='complete'
changed=[]
paths=set(ROOT.rglob('REMOTE_DATA_LOCATION.md'))
for project in ROOT.parent.iterdir():
 if project.is_dir():
  for name in ['REMOTE_RESEARCH_DATA.md','RESEARCH_DATA_REMOTE.md']:
   p=project/name
   if p.exists():paths.add(p)
for p in sorted(paths):
 if 'local_originals' in p.parts:continue
 old=p.read_text()
 if '192.168.3.36' not in old and '/home/varmak/research_data' not in old:continue
 backup=OUT/'local_originals'/p.relative_to(ROOT.parent)
 backup.parent.mkdir(parents=True,exist_ok=True)
 if not backup.exists():shutil.copy2(p,backup)
 new=old.replace('varmak@192.168.3.36','varma@192.168.3.136').replace('/home/varmak/research_data','/home/varma/research_data').replace('(`ashish-server`)','(`ashish-home`)')
 new=new.replace('/home/varma/research_data/StocksTradingTest/.venv','/home/varma/research_runtime').replace('/home/varma/research_data/StocksTradingTest/research-python','/home/varma/research-python').replace('/home/varma/research_data/research-run','/home/varma/research-run')
 new=new.replace('ssh varma@192.168.3.136','ssh -i ~/.ssh/id_ed25519_migration_192_168_3_136 -o IdentitiesOnly=yes varma@192.168.3.136')
 new+='\nServer relocation, 2026-09-26: current paths above use the new server. Original September 14 offload counts and receipts remain historical records. The original old-server account is preserved under `/home/varma/old_server_archive_2026_09_26/home/varmak`; see `/home/varma/old_server_archive_2026_09_26/audit/` for migration verification.\n'
 p.write_text(new);changed.append(str(p))
p=ROOT/'research_migration_2026_09_14/run_remote.py';s=p.read_text()
s=s.replace('import subprocess,sys,shlex,json','import subprocess,sys,shlex,json\nfrom pathlib import Path\nSSH=[\'ssh\',\'-i\',str(Path.home()/\'.ssh/id_ed25519_migration_192_168_3_136\'),\'-o\',\'IdentitiesOnly=yes\',\'-o\',\'BatchMode=yes\']')
s=s.replace("runner='/home/varmak/research_data/StocksTradingTest/research-python';host='varmak@192.168.3.36'","runner='/home/varma/research-python';host='varma@192.168.3.136'")
s=s.replace("['ssh','-o','BatchMode=yes',host,",'[*SSH,host,');p.write_text(s);changed.append(str(p))
p=ROOT/'research_migration_2026_09_14/restore_data.py';s=p.read_text()
s=s.replace('import argparse,json,subprocess,hashlib,shutil','import argparse,json,subprocess,hashlib,shutil,shlex')
s=s.replace("'ssh -o BatchMode=yes'","shlex.join(['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes'])")
s=s.replace('varmak@192.168.3.36:/home/varmak/research_data','varma@192.168.3.136:/home/varma/research_data');p.write_text(s);changed.append(str(p))
(OUT/'local_access_changes.json').write_text(json.dumps(changed,indent=2))
print(json.dumps({'updated_files':len(changed)}))
