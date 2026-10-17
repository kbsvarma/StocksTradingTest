from pathlib import Path
import json,subprocess,socket,time,os
ROOT=Path('/home/varma/migration_2026_09_25')
def run(args):
 env=dict(os.environ)
 if args[0]=='ldd':env['LD_LIBRARY_PATH']=str(jre/'lib')+':'+str(jre/'lib/server')
 r=subprocess.run(args,text=True,capture_output=True,env=env);return {'code':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
jre=Path('/home/varma/.local/share/i4j_jres/Oda-jK0QgTEmVssfllLP/17.0.16.0.101-zulu_64')
# Gateway installers retained stale source-machine JRE paths. Override only deployment wrappers.
wrapper=Path('/home/varma/trendbot/start_gateway.sh');s=wrapper.read_text()
if '--java-path=' not in s:
 wrapper.with_suffix('.sh.before-migration-jre').write_text(s)
 wrapper.write_text(s.replace('--mode=paper',f'--java-path={jre}/bin --mode=paper'))
wrapper.write_text(wrapper.read_text().replace(f'--java-path={jre} --mode=paper',f'--java-path={jre}/bin --mode=paper'))
# Verify a headless display without launching or logging into the gateway.
log=(ROOT/'xvfb-smoke.log').open('w')
p=subprocess.Popen(['Xvfb',':199','-screen','0','1024x768x24','-nolisten','tcp'],stdout=log,stderr=log)
try:
 time.sleep(2);display_ok=p.poll() is None
finally:
 if p.poll() is None:p.terminate();p.wait(timeout=10)
 log.close()
result={'host':socket.gethostname(),'time':time.time(),'xvfb_smoke':display_ok,'java':run([str(jre/'bin/java'),'-version']),'java_awt_dependencies':run(['ldd',str(jre/'lib/libawt_xawt.so')]),'python':run(['/home/varma/.local/bin/python3.12','--version']),'linger':run(['loginctl','show-user','varma','-p','Linger']),'backup_mount':run(['findmnt','-rn','-T','/var/backups/openstaff-ats/archives','-o','TARGET,SOURCE,UUID']),'opt_entries':list(map(str,Path('/opt').iterdir())),'containers_removed':json.loads((ROOT/'cleanup_result.json').read_text())['removed_containers'],'gateway_started':False}
(ROOT/'destination_verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='java_awt_dependencies'},indent=2))
assert display_ok and 'not found' not in result['java_awt_dependencies']['stdout']
