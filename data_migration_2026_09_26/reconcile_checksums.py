"""Repair files identified by the complete checksum pass, then recheck each repair."""
from pathlib import Path
import subprocess,json,time,shutil,re
H=Path('/home/varma');C=H/'.data_migration_control_20260926';A=H/'old_server_archive_2026_09_26';D=A/'home/varmak'
v=json.loads((C/'verification_result.json').read_text());assert v['returncode']==0,v
log=C/'checksum-differences.txt';lines=log.read_text().splitlines()
assert lines,'No differences to reconcile'
paths=[]
for line in lines:
 assert re.match(r'^[<>.ch][fdLDS][^ ]{9} ',line),repr(line)
 rel=line[12:].split(' -> ',1)[0] if line[1]=='L' else line[12:]
 assert rel and not rel.startswith('/') and '..' not in Path(rel).parts and '\\' not in rel,repr(rel)
 assert rel.endswith('-shm') or line.startswith(('.d..t...... ','.L..t...... ')), 'Inspect unexpected difference before repair: '+line
 paths.append(rel)
(A/'audit/checksum_initial_differences.txt').write_text(log.read_text())
(A/'audit/checksum_initial_result.json').write_text(json.dumps(v,indent=2))
files=C/'reconcile-files.nul';files.write_bytes(b''.join(p.encode()+b'\0' for p in paths))
ssh=f'ssh -i {C}/source_readonly_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={C}/known_hosts'
base=['rsync','-aH','--checksum','--no-owner','--no-group','--no-devices','--no-specials','--from0','--files-from='+str(files),'-e',ssh,'varmak@192.168.3.36:./',str(D)+'/']
with (C/'reconcile.log').open('w') as out:
 r=subprocess.run(base,stdout=out,stderr=subprocess.STDOUT)
assert r.returncode==0,r.returncode
with (C/'reconcile-differences.txt').open('w') as out:
 r=subprocess.run(base[:1]+['--dry-run','--itemize-changes','--out-format=%i %n%L']+base[1:],stdout=out,stderr=subprocess.STDOUT)
assert r.returncode==0,r.returncode
assert (C/'reconcile-differences.txt').stat().st_size==0,(C/'reconcile-differences.txt').read_text()
receipt={'finished':time.time(),'returncode':0,'difference_log_bytes':0,'method':'Complete source/destination checksum pass; listed differences repaired and independently checksum-rechecked.','repaired_paths':paths,'initial_result':v}
(A/'audit/checksum_reconciliation.json').write_text(json.dumps(receipt,indent=2))
(C/'verification_result.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps({'repaired_and_rechecked':len(paths)}))
