"""Install verified cold state without enabling trading services."""
from pathlib import Path
import hashlib,json,subprocess,shutil,time
H=Path('/home/varma');R=H/'migration_2026_09_25';B=R/'migration-extra-20260925-cold'
assert (H/'MIGRATION_HOLD').exists()
if (R/'cold_restore_receipt.json').exists():raise SystemExit('Already restored; inspect receipt before repeating.')
m=json.loads((B/'manifest.json').read_text());assert m['mode']=='cold'
for r in m['files']:
 if 'path' in r:assert hashlib.sha256((B/r['path']).read_bytes()).hexdigest()==r['sha256'],r['path']
kit=R/'kit';stage=R/'trend-cold-stage'
subprocess.run(['python3',str(kit/'migrate.py'),'stage',str(R/'trend-migration-20260925-cold'),'--destination',str(stage),'--target-home',str(H),'--target-user','varma'],check=True)
subprocess.run(['python3',str(kit/'install_staged.py'),str(stage),'--refresh-cold'],check=True)
backup=R/('warm-payload-'+str(int(time.time())));backup.mkdir(mode=0o700)
for name,dest in [('advisor',H/'stockstest/advisor'),('kalshi_crypto',H/'kalshi_revalidation_2026_09_12/live_v1')]:
 dest.rename(backup/name);shutil.copytree(B/name,dest)
k=H/'kalshi_revalidation_2026_09_12/live_v1'
for f in [k/'runner.py',k/'dashboard_panel.py',k/'dashboard_crypto.py']:
 f.write_text(f.read_text().replace('/home/varmak','/home/varma'))
# Source arm files were intentionally not copied. Systemd hold stays in place.
for n in ['ARM_1CT','ARM_2CT']:assert not (k/'state'/n).exists()
subprocess.run(['python3',str(R/'verify_destination.py')],stdout=subprocess.DEVNULL,check=True)
# Keep original broker settings for user activation; preflight uses read-only API.
config=H/'ibc/config.ini';saved=R/'ibc-config-before-readonly.ini';shutil.copy2(config,saved);saved.chmod(0o600)
s=config.read_text();assert 'ReadOnlyApi=no' in s
config.write_text(s.replace('ReadOnlyApi=no','ReadOnlyApi=yes'));config.chmod(0o600)
receipt={'time':time.time(),'extra_manifest_sha256':hashlib.sha256((B/'manifest.json').read_bytes()).hexdigest(),'trend_manifest_sha256':hashlib.sha256((R/'trend-migration-20260925-cold/manifest.json').read_bytes()).hexdigest(),'source_shutdown_receipt':json.loads((R/'migration_source_shutdown_20260925.json').read_text())['time'],'trading_started':False}
(R/'cold_restore_receipt.json').write_text(json.dumps(receipt,indent=2))
print('Cold state restored; source arm files absent; destination trading remains held; gateway configured read-only for reconciliation.')
