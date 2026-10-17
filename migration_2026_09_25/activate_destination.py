"""USER-RUN ONLY: activate migrated trading services after source shutdown."""
import argparse,json,os,shutil,socket,subprocess,time
from pathlib import Path
H=Path('/home/varma');R=H/'migration_2026_09_25'
USER=['runuser','-u','varma','--','env','XDG_RUNTIME_DIR=/run/user/1001','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus','systemctl','--user']
def run(args,**kw):return subprocess.run(args,check=True,**kw)
def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');args=p.parse_args()
 if not args.execute or os.geteuid()!=0 or socket.gethostname()!='ashish-home':raise SystemExit('Run with sudo --execute on ashish-home.')
 if (R/'activation_receipt.json').exists():raise SystemExit('Already activated; do not repeat. Inspect running services.')
 assert (H/'MIGRATION_HOLD').exists() and (R/'cold_restore_receipt.json').exists()
 proof=json.loads((R/'source_stopped_proof.json').read_text())
 if time.time()-proof['checked_at']>300 or not proof['all_stopped']:raise SystemExit('Fresh old-server shutdown verification required; run the Mac activation script.')
 brokerpy=str(H/'trendbot/trend_bot/.venv/bin/python')
 run(['runuser','-u','varma','--',brokerpy,str(R/'probe_broker.py')])
 run(['runuser','-u','varma','--',str(H/'kalshi/venv/bin/python'),'runner.py','--preflight'],cwd=H/'kalshi_revalidation_2026_09_12/live_v1')
 # This explicit user-run step restores the original gateway write setting and arms the bot.
 shutil.copy2(R/'ibc-config-before-readonly.ini',H/'ibc/config.ini');os.chown(H/'ibc/config.ini',1001,1001);(H/'ibc/config.ini').chmod(0o600)
 (H/'MIGRATION_HOLD').unlink()
 for unit in ['trendbot-xvfb.service','trendbot-ibc.service']:
  (Path('/etc/systemd/system')/(unit+'.d')/'migration-readonly.conf').unlink(missing_ok=True)
 run(['systemctl','daemon-reload']);run(USER+['daemon-reload'])
 run(['systemctl','enable','trendbot-xvfb.service','trendbot-ibc.service'])
 run(['systemctl','restart','trendbot-ibc.service'])
 # Gateway login must complete before any trading daemon starts.
 ready=False
 for _ in range(18):
  time.sleep(5)
  check=subprocess.run(['runuser','-u','varma','--',brokerpy,str(R/'probe_broker.py')],capture_output=True,text=True)
  if check.returncode==0:ready=True;break
 if not ready:
  (H/'MIGRATION_HOLD').write_text('Gateway login/reconciliation did not complete; trading remains disabled.\n')
  raise SystemExit('Gateway needs attention. No trading daemon started; inspect gateway before retrying.')
 arm=H/'kalshi_revalidation_2026_09_12/live_v1/state/ARM_2CT';arm.write_text('2\n');os.chown(arm,1001,1001);arm.chmod(0o600)
 original=json.loads((R/'migration_source_shutdown_20260925.json').read_text());started=[]
 try:
  for scope,cmd in [('system',['systemctl']),('user',USER)]:
   for unit,s in original[scope].items():
    if unit in ['advisor-model.service','trendbot-ibc.service','trendbot-xvfb.service']:continue
    if unit=='kalshi-dashboard.service':continue # Moved from system to user scope.
    if s['enabled']=='enabled':run(cmd+['enable',unit])
    if s['active']=='active' or s['enabled']=='enabled':run(cmd+['start',unit]);started.append((scope,unit))
  run(USER+['enable','--now','kalshi-dashboard.service']);started.append(('user','kalshi-dashboard.service'))
 except Exception:
  (H/'MIGRATION_HOLD').write_text('Partial activation; inspect services before retrying.\n')
  (R/'activation_partial.json').write_text(json.dumps({'started':started,'time':time.time()},indent=2))
  raise
 (R/'activation_receipt.json').write_text(json.dumps({'time':time.time(),'started':started,'source_proof':proof,'contracts':2},indent=2))
 print('Destination services activated. Old source remains stopped. Verify dashboards and fresh bot heartbeats.')
if __name__=='__main__':main()
