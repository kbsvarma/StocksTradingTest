"""Install only the dashboard presentation; preserve trader code and flags."""
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

P = Path(__file__).resolve().parent
L = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
NAME = 'dashboard_panel.py'

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def snapshot():
    units = ('kalshi-crypto-z2-live.service', 'kalshi-dashboard.service',
             'kalshi-tennis90-paper.service', 'kalshi-resting-paper.service')
    return dict(source={p.name: digest(p) for p in L.glob('*.py')},
                flags={p.name: digest(p) for p in (L/'state').iterdir()
                       if p.name.startswith(('ARM', 'HALT'))},
                services={u: subprocess.check_output(
                    ['systemctl', '--user', 'show', u, '-p', 'MainPID', '-p', 'ActiveState'],
                    text=True) for u in units})

before = snapshot()
assert not (P/'deployment.json').exists()
assert digest(L/NAME) == digest(P/'original'/NAME), 'Dashboard changed since capture'
assert 'HALT_USDCAD' in before['flags']
compile((P/'candidate'/NAME).read_text(), NAME, 'exec')
(P/'before.json').write_text(json.dumps(before, indent=2)+'\n')

def replace_from(folder):
    tmp = L/(NAME+'.ui-update')
    tmp.write_bytes((P/folder/NAME).read_bytes())
    tmp.replace(L/NAME)
    subprocess.run(['systemctl', '--user', 'restart', 'kalshi-dashboard.service'], check=True)

try:
    replace_from('candidate')
    healthy = False
    for _ in range(20):
        try:
            healthy = urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health', timeout=2).read() == b'ok'
            if healthy:
                break
        except OSError:
            pass
        time.sleep(.5)
    assert healthy, 'Dashboard health failed'
except Exception:
    replace_from('original')
    raise
after = snapshot()
assert before['flags'] == after['flags']
assert all(after['source'][n] == h for n,h in before['source'].items() if n != NAME)
assert all(after['services'][u] == s for u,s in before['services'].items() if u != 'kalshi-dashboard.service')
receipt = dict(ts=time.time(), changed=[NAME], before=before, after=after, ui_health=healthy,
               trader_and_paper_services_unchanged=True, trading_source_and_flags_unchanged=True)
(P/'deployment.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps({k:v for k,v in receipt.items() if k not in ('before','after')}))
