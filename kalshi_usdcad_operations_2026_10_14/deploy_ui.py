"""UI-only install. Never starts/stops the trader or modifies its state/configuration."""
import hashlib,json,subprocess,time,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
L=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
FILES=('dashboard_quotes.py','dashboard_panel.py')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def service(n):return dict(r.split('=',1) for r in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
def flags():return {p.name:digest(p) for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))}
def save(p,d):p.write_text(json.dumps(d,indent=2))
original=json.loads((P/'preservation_before.json').read_text())
assert not (P/'ui_deployment.json').exists(),'Already installed; inspect receipt before any retry'
assert all(digest(L/n)==h for n,h in original['source'].items()),'Production changed since snapshot'
assert flags()==original['flags']
services={n:service(n) for n in original['services']}
assert all(services[n]==original['services'][n] for n in services if n!='kalshi-dashboard.service')
for n in FILES:
    assert digest(P/'original'/n)==digest(L/n)
    compile((P/'candidate'/n).read_text(),n,'exec')
if (P/'ui_deployment_started.json').exists():
    (P/'ui_deployment_started.json').rename(P/('ui_deployment_prior_started_'+str(int(time.time()))+'.json'))
save(P/'ui_deployment_started.json',dict(ts=time.time(),services=services,files={n:digest(P/'candidate'/n) for n in FILES}))
try:
    for n in FILES:
        temp=L/(n+'.ui-update');temp.write_bytes((P/'candidate'/n).read_bytes());temp.replace(L/n)
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True,timeout=30)
    healthy=False
    for _ in range(20):
        try:
            healthy=urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=2).read()==b'ok'
            if healthy:break
        except OSError:pass
        time.sleep(.5)
    assert healthy,'UI health endpoint failed'
except Exception:
    for n in FILES:
        temp=L/(n+'.ui-rollback');temp.write_bytes((P/'original'/n).read_bytes());temp.replace(L/n)
    subprocess.run(['systemctl','--user','restart','kalshi-dashboard.service'],check=True,timeout=30)
    raise
assert flags()==original['flags']
assert all(digest(L/n)==h for n,h in original['source'].items() if n not in FILES)
after={n:service(n) for n in services}
assert all(after[n]==services[n] for n in services if n!='kalshi-dashboard.service')
save(P/'ui_deployment.json',dict(ts=time.time(),changed=list(FILES),services_before=services,services_after=after,trading_sources_unchanged=True,flags_unchanged=True,ui_health=healthy))
print(json.dumps(dict(ui_health=healthy,changed=list(FILES),trader_pid=after['kalshi-crypto-z2-live.service']['MainPID'],other_services_unchanged=True)))
