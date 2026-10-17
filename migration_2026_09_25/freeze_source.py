"""Stop only the source services included in this migration; no broker orders."""
import json,os,socket,subprocess,time
from pathlib import Path
if os.geteuid()!=0 or socket.gethostname()!='ashish-server':raise SystemExit('Run as root on ashish-server only.')
sysunits=['trendbot.service','trendbot-ibc.service','trendbot-xvfb.service','trendbot-dashboard.service','trendbot-watchdog.service','trendbot-watchdog.timer','kalshi-dashboard.service']
h=Path('/home/varmak');uid=1002
base=['runuser','-u','varmak','--','env',f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus','systemctl','--user']
userunits=[p.name for p in (h/'.config/systemd/user').iterdir() if p.is_file() and (p.name.startswith('advisor-') or p.name.startswith('trendbot-shadow') or p.name.startswith('kalshi-crypto-z2-')) and p.suffix in ['.service','.timer']]
def state(cmd,name):
 return {k:subprocess.run(cmd+[op,name],capture_output=True,text=True).stdout.strip() for k,op in [('active','is-active'),('enabled','is-enabled')]}
receipt=h/'migration_source_shutdown_20260925.json'
if receipt.exists():raise SystemExit('Shutdown receipt already exists; inspect before repeating.')
before={'time':time.time(),'system':{n:state(['systemctl'],n) for n in sysunits},'user':{n:state(base,n) for n in userunits}}
receipt.write_text(json.dumps(before,indent=2));os.chown(receipt,uid,uid);receipt.chmod(0o600)
# First prevent schedules/watchdogs from spawning new writers.
for cmd,names in [(base,userunits),(['systemctl'],sysunits)]:
 for n in sorted(names,key=lambda n:(0 if n.endswith('.timer') or 'watchdog' in n else 1)):
  subprocess.run(cmd+['disable','--now',n],check=True)
for cmd,names in [(base,userunits),(['systemctl'],sysunits)]:
 for n in names:
  s=state(cmd,n)
  if s['active'] not in ['inactive','failed','unknown'] or s['enabled'] not in ['disabled','static','not-found','']:raise RuntimeError('Source not frozen: '+n+' '+str(s))
(h/'MIGRATION_SOURCE_FROZEN_20260925').write_text(str(time.time()))
print('Source migration services stopped and disabled. No broker orders changed. Other source bots were not touched.')
