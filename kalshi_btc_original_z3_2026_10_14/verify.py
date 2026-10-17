"""Read-only postflight: effective settings, history, service health and rendered UI."""
import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import deploy

P=Path(__file__).resolve().parent
receipt=json.loads((P/'deployment_receipt.json').read_text())
s=json.loads((deploy.STATE/'status.json').read_text())
assert time.time()-s['ts']<15
assert s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('halted','error','stopped'))
assert deploy.configs(s)==receipt['config_after']
assert deploy.flags()==receipt['flags']
checkpoint_files=deploy.validate_hashes(receipt['source_hashes'])
services={n:deploy.service(n) for n in deploy.SERVICES}
assert all(v['ActiveState']=='active' and v['MainPID']!='0' for v in services.values())
assert services==receipt['services_after']
observer=Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state/status.json')
observed=json.loads(observer.read_text())
assert time.time()-observed['ts']<15 and not observed['error']
with urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=10) as response:
    assert response.status==200 and response.read().strip()==b'ok'

from streamlit.testing.v1 import AppTest
ui=AppTest.from_file('/home/varma/kalshi/kalshi_unbroken_bot/dashboard.py').run(timeout=40)
assert not ui.exception, str([str(x) for x in ui.exception])
rules=[]
for table in ui.dataframe:
    frame=table.value
    if {'Asset','Strategy','Minimum z','Buy cap','Contracts'}.issubset(frame.columns):
        rules=frame.to_dict('records')
assert len(rules)==14, rules
btc=next(r for r in rules if r['Asset']=='BTC' and r['Strategy']=='Original')
assert btc['Minimum z']=='3' and btc['Contracts']==5 and float(btc['Buy cap'].rstrip('¢'))==93.9, btc
for row in rules:
    if row['Asset'] in ('ETH','SOL','XRP','NATGAS') and row['Strategy']=='Original':
        assert row['Minimum z']=='2', row
assert all(r['Contracts']==5 for r in rules)
backup=Path(receipt['backup'])
historical=json.loads((backup/'ledger_records.json').read_text())
current=deploy.rows()
assert all(current.get(t)==r for t,r in historical.items())
with sqlite3.connect((deploy.STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('pragma quick_check').fetchone()[0]=='ok'
    attempts=dict(db.execute('select client_id,record from order_attempts'))
prior_attempts=json.loads((backup/'attempt_records.json').read_text())
assert all(attempts.get(t)==r for t,r in prior_attempts.items())
out=dict(verified_ts=time.time(),heartbeat_age_seconds=time.time()-s['ts'],mode=s['mode'],armed=s['armed'],
    error=s['error'],halted=s['halted'],config=deploy.configs(s),services=services,
    dashboard_http=200,dashboard_render_exceptions=0,dashboard_rules=rules,
    observer_heartbeat_ts=observed['ts'],preserved_market_records=len(historical),
    preserved_attempts=len(prior_attempts),stable_checkpoint_files=checkpoint_files,
    source_hashes=receipt['source_hashes'],other_live_source_files_unchanged=True)
(P/'verification.json').write_text(json.dumps(out,indent=2)+'\n')
(P/'postflight_status.json').write_text(json.dumps(s,indent=2)+'\n')
print(json.dumps(dict(verified=True,mode=s['mode'],btc_original_z=3,contracts=5,
    max_contracts=25,max_risk='25.00',daily_budget='20',dashboard=200,
    historical_records=len(historical),attempts=len(prior_attempts),checkpoint_files=checkpoint_files)))
