"""Resumable read-only pull into a separate archive; no deletion of source or target."""
import subprocess,json,time
from pathlib import Path
H=Path('/home/varma');C=H/'.data_migration_control_20260926';C.mkdir(mode=0o700,exist_ok=True)
D=H/'old_server_archive_2026_09_26/home/varmak'
ssh=f'ssh -i {C}/source_readonly_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={C}/known_hosts -o ServerAliveInterval=30 -o ServerAliveCountMax=6'
base=['rsync','-aH','--no-owner','--no-group','--no-devices','--no-specials','--exclude=/.data_migration_control_20260926/','--exclude=/.ssh/authorized_keys','-e',ssh,'varmak@192.168.3.36:./',str(D)+'/']
started=time.time()
with (C/'transfer.log').open('a') as log:
 r=subprocess.run(base[:1]+['--partial','--partial-dir=.rsync-partial','--info=progress2,stats2','--outbuf=L']+base[1:],stdout=log,stderr=log)
(C/'transfer_result.json').write_text(json.dumps({'started':started,'finished':time.time(),'returncode':r.returncode,'destination':str(D)},indent=2))
if r.returncode:raise SystemExit(r.returncode)
with (C/'checksum-differences.txt').open('w') as log:
 v=subprocess.run(base[:1]+['--dry-run','--checksum','--itemize-changes','--out-format=%i %n%L']+base[1:],stdout=log,stderr=log)
(C/'verification_result.json').write_text(json.dumps({'finished':time.time(),'returncode':v.returncode,'difference_log_bytes':(C/'checksum-differences.txt').stat().st_size},indent=2))
raise SystemExit(v.returncode)
