from pathlib import Path
import shutil,subprocess,shlex,json
H=Path('/home/varma');A=H/'oncopulse-deployment/oncopulse';L=H/'oncopulse_live'
if L.exists():raise SystemExit('Live directory already exists; inspect before retry.')
L.mkdir(mode=0o700)
shutil.copytree(A,L/'app',ignore=shutil.ignore_patterns('.venv','.git','__pycache__','.DS_Store'))
(L/'data').mkdir();shutil.copy2(H/'oncopulse_data/oncopulse.db',L/'data/oncopulse.db')
U=H/'old_server_archive_2026_09_26/additional_projects/oncopulse/full_export/extracted/oncopulse_export/files/etc/systemd/system/oncopulse.service'
env={}
for line in U.read_text().splitlines():
 if line.startswith('Environment='):
  for item in shlex.split(line.split('=',1)[1]):
   k,sep,v=item.partition('=')
   if sep:env[k]=v
# Keep application configuration; point the database to the independent live copy.
env['ONCOPULSE_DB_PATH']=str(L/'data/oncopulse.db')
p=L/'environment';p.write_text('\n'.join(k+'='+json.dumps(v) for k,v in env.items())+'\n');p.chmod(0o600)
subprocess.run([str(H/'.local/bin/python3.12'),'-m','venv',str(L/'.venv')],check=True)
print(json.dumps({'staged':str(L),'environment_keys':list(env)}))
