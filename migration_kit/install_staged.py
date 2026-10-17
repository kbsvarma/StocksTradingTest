#!/usr/bin/env python3
"""Place a staged Trend payload on a NEW destination. Never starts services."""
import argparse,json,shutil,socket
from datetime import datetime,timezone
from pathlib import Path
from migrate import SYSTEM,USER,unit_state
p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',type=Path);p.add_argument('--refresh-cold',action='store_true');a=p.parse_args();m=json.loads((a.stage/'stage.json').read_text());home=Path(m['target_home']);source=a.stage/'home'
if socket.gethostname()==m['source_host']:raise SystemExit('Refusing to restore onto the running source host.')
if home!=Path.home():raise SystemExit('Run as the chosen destination user; target home must match HOME.')
units=[unit_state(False,n) for n in SYSTEM]+[unit_state(True,n) for n in USER]
if any(u['active'] not in ['inactive','failed','unknown'] or u['enabled'] not in ['disabled','masked','static','not-found',''] for u in units):raise SystemExit('Destination Trend services/timers must remain stopped and disabled.')
roots=[Path('trendbot'),Path('ibc'),Path('Jts/ibgateway'),Path('Jts/jars')];backup=None
if a.refresh_cold:
 if not m['activation_eligible'] or m['source_mode']!='cold':raise SystemExit('Only a verified cold snapshot can replace a warm preparation.')
 backup=home/('trend-migration-rollback-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir(mode=0o700)
else:
 for root in roots:
  if (home/root).exists():raise SystemExit(f'Refusing to overwrite existing destination: {home/root}; use --refresh-cold only for final cold cutover.')
marker=home/'TREND_MIGRATION_INCOMPLETE';marker.write_text('Services must not start until migration completes.')
for root in roots:
 if backup and (home/root).exists():
  (backup/root).parent.mkdir(parents=True,exist_ok=True);(home/root).rename(backup/root)
 (home/root).parent.mkdir(parents=True,exist_ok=True);shutil.copytree(source/root,home/root,symlinks=True)
if backup:
 venv=backup/'trendbot/trend_bot/.venv'
 if venv.exists():venv.rename(home/'trendbot/trend_bot/.venv')
 private=backup/'ibc/config.ini'
 if private.exists():shutil.copy2(private,home/'ibc/config.ini');(home/'ibc/config.ini').chmod(0o600)
receipt=dict(source_host=m['source_host'],target_host=socket.gethostname(),restored_at=datetime.now(timezone.utc).isoformat(),bundle_manifest_sha256=m['bundle_manifest_sha256'],source_mode=m['source_mode'],shadow_epoch_preserved=True,advisor='excluded')
receipts=home/'trendbot/shadow_capture/state/migrations';receipts.mkdir(parents=True,exist_ok=True);(receipts/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')).write_text(json.dumps(receipt,indent=2))
marker.unlink();print('Payload restored. No units installed, enabled or started. This is '+m['source_mode']+' data.')
if backup:print('Previous prepared payload retained at '+str(backup))
if not m['activation_eligible']:print('WARM PREPARATION ONLY: final stopped-source cold snapshot is still required before cutover.')
