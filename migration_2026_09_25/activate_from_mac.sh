#!/usr/bin/env bash
set -euo pipefail
# USER-RUN: verifies old source is stopped, then requests destination activation.
set -o pipefail
ssh varmak@192.168.3.36 python3 - <<'PY' | ssh -i "$HOME/.ssh/id_ed25519_migration_192_168_3_136" varma@192.168.3.136 'umask 077; cat > /home/varma/migration_2026_09_25/source_stopped_proof.json'
import json,subprocess,time
from pathlib import Path
r=json.loads(Path('/home/varmak/migration_source_shutdown_20260925.json').read_text())
for scope in ['system','user']:
 for name in r[scope]:
  base=['systemctl']+(['--user'] if scope=='user' else [])
  active=subprocess.run(base+['is-active',name],capture_output=True,text=True).stdout.strip()
  enabled=subprocess.run(base+['is-enabled',name],capture_output=True,text=True).stdout.strip()
  if active not in ['inactive','failed','unknown'] or enabled not in ['disabled','static','not-found','']:raise SystemExit('Source service not frozen: '+name)
print(json.dumps({'checked_at':time.time(),'all_stopped':True,'source':'192.168.3.36'}))
PY
ssh -tt -o PubkeyAuthentication=no -o PreferredAuthentications=password ashish@192.168.3.136 'sudo python3 /home/varma/migration_2026_09_25/activate_destination.py --execute'
