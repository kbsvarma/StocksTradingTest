"""Finalize an independently verified archive and remove the temporary transfer key."""
from pathlib import Path
import subprocess,json,hashlib,shlex
SSH=['ssh','-i',str(Path.home()/'.ssh/id_ed25519_migration_192_168_3_136'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','varma@192.168.3.136']
C='/home/varma/.data_migration_control_20260926'
A='/home/varma/old_server_archive_2026_09_26'
def remote(code,data=None):
 return subprocess.check_output(SSH+['python3 -c '+shlex.quote(code)],input=data)
s=json.loads(remote(f'from pathlib import Path;print(Path({C+"/postverify_status.json"!r}).read_text())'))
assert s['stage']=='verified_awaiting_final_handover',s
code="""from pathlib import Path
import os
p=Path('/home/varmak/.ssh/authorized_keys')
raw=p.read_bytes();lines=raw.splitlines(keepends=True)
needle=b'codex-data-migration-20260926'
removed=[line for line in lines if needle in line]
assert len(removed)<=1,'Unexpected duplicate key marker'
if removed:
 assert b'command="/usr/bin/rrsync -ro /home/varmak"' in removed[0]
 new=b''.join(line for line in lines if needle not in line)
 st=p.stat();tmp=p.with_name('authorized_keys.migration-cleanup');tmp.write_bytes(new);tmp.chmod(st.st_mode & 0o777);tmp.replace(p)
print('Temporary key removed' if removed else 'Temporary key already absent')
"""
print(subprocess.check_output(['ssh','-o','BatchMode=yes','varmak@192.168.3.36','python3 -c '+shlex.quote(code)],text=True).strip())
raw=subprocess.check_output(['ssh','-o','BatchMode=yes','varmak@192.168.3.36','cat /home/varmak/.ssh/authorized_keys'])
expected=hashlib.sha256(raw).hexdigest()
code=f"""from pathlib import Path
import sys,hashlib,json,time
A=Path({A!r});C=Path({C!r});raw=sys.stdin.buffer.read()
p=A/'home/varmak/.ssh/authorized_keys';p.write_bytes(raw);p.chmod(0o600)
actual=hashlib.sha256(p.read_bytes()).hexdigest();assert actual=={expected!r}
(A/'audit/authorized_keys_verified.json').write_text(json.dumps({{'time':time.time(),'sha256':actual,'temporary_key_removed':True}},indent=2))
for name in ['source_readonly_key','source_readonly_key.pub']:(C/name).unlink(missing_ok=True)
print('Cleaned authorized_keys independently verified; temporary key files removed.')
"""
print(remote(code,raw).decode().strip())
subprocess.run(SSH+['python3 '+C+'/finalize.py'],check=True)
code=f'''from pathlib import Path
C=Path({C!r})
for name in ['research-run','research-python']:
 p=Path('/home/varma')/name;body=(C/name).read_bytes()
 if p.exists() or p.is_symlink():
  assert p.is_file() and p.read_bytes()==body,'Existing research runner must not be overwritten: '+str(p)
 else:p.write_bytes(body)
 p.chmod(0o755)
'''
remote(code)
subprocess.run(SSH+['/home/varma/research-python '+C+'/research_smoke.py'],check=True)
print('Archive and research runtime verified.')
