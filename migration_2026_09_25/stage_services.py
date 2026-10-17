from pathlib import Path
import shutil,json,subprocess
H=Path('/home/varma');R=H/'migration_2026_09_25';B=R/'migration-extra-20260925';SUP=R/'support'
(H/'MIGRATION_HOLD').write_text('Destination services blocked pending final cold state and user activation.\n')
stock=H/'stockstest';stock.mkdir(exist_ok=True)
if not (stock/'advisor').exists():shutil.copytree(B/'advisor',stock/'advisor')
if not (stock/'.venv').exists():(stock/'.venv').symlink_to(R/'test-advisor/.venv',target_is_directory=True)
k=H/'kalshi_revalidation_2026_09_12/live_v1';k.parent.mkdir(parents=True,exist_ok=True)
if not k.exists():shutil.copytree(B/'kalshi_crypto',k)
(H/'kalshi').mkdir(exist_ok=True)
if not (H/'kalshi/venv').exists():(H/'kalshi/venv').symlink_to(R/'test-kalshi_crypto/.venv',target_is_directory=True)
legacy=H/'kalshi/kalshi_unbroken_bot'
shutil.copytree(SUP/'kalshi/kalshi_unbroken_bot',legacy,dirs_exist_ok=True)
for f in [k/'runner.py',k/'dashboard_panel.py',k/'dashboard_crypto.py',legacy/'dashboard.py']:
 f.write_text(f.read_text().replace('/home/varmak','/home/varma'))
(k/'state/HALT').write_text('Migration staging only\n')
for arm in ['ARM_1CT','ARM_2CT']:(k/'state'/arm).unlink(missing_ok=True)
shutil.copy2(SUP/'advisor_backup.sh',H/'advisor_backup.sh')
for p in ['backups/advisor','stockstest/advisor/logs']:(H/p).mkdir(parents=True,exist_ok=True)
units=H/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
for f in (B/'units').iterdir():
 if not f.is_file():continue
 # The local model is inactive on source; Advisor's current provider is Gemini.
 if f.name=='advisor-model.service':continue
 text=f.read_text().replace('/home/varmak','/home/varma').replace('192.168.3.36','192.168.3.136')
 text=text.replace('[Unit]','[Unit]\nConditionPathExists=!/home/varma/MIGRATION_HOLD',1)
 if f.name=='kalshi-crypto-z2-live.service':text=text.replace('1 contract','2 contracts')
 (units/f.name).write_text(text)
for d in (SUP/'.config/systemd/user').glob('*.service.d'):shutil.copytree(d,units/d.name,dirs_exist_ok=True)
(units/'kalshi-dashboard.service').write_text('''[Unit]
Description=Kalshi crypto dashboard
ConditionPathExists=!/home/varma/MIGRATION_HOLD
After=network-online.target
[Service]
WorkingDirectory=/home/varma/kalshi/kalshi_unbroken_bot
ExecStart=/home/varma/kalshi/venv/bin/python -m streamlit run dashboard.py --server.port 8507 --server.headless true --server.address 192.168.3.136
Restart=on-failure
[Install]
WantedBy=default.target
''')
# Stage guarded system unit files for a later root installation. Do not enable them.
for f in (R/'trend-stage/units/system').iterdir():
 s=f.read_text()
 if 'MIGRATION_HOLD' not in s:f.write_text(s.replace('[Unit]','[Unit]\nConditionPathExists=!/home/varma/MIGRATION_HOLD',1))
for f in (R/'trend-stage/units/user').iterdir():
 s=f.read_text().replace('[Unit]','[Unit]\nConditionPathExists=!/home/varma/MIGRATION_HOLD',1);(units/f.name).write_text(s)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
print('Destination paths and guarded user services staged. No services enabled or started.')
