"""Read-only verification after the user executes activation."""
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen
import activate

P=Path(__file__).resolve().parent
receipt=json.loads((P/'deployment_receipt.json').read_text())
assert receipt['activated']
assert activate.flags()==receipt['flags']
checkpoint=activate.validate_hashes(receipt['source_hashes'])
services={n:activate.service(n) for n in activate.RESTART+activate.PRESERVE}
assert services==receipt['services_after']
assert all(services[n]['ActiveState']=='active' for n in activate.RESTART)
assert services['kalshi-weather-early-paper.service']['ActiveState']=='inactive'
with urlopen('http://192.168.3.136:8507/_stcore/health',timeout=10) as response:
    assert response.status==200 and response.read().strip()==b'ok'
sys.path.insert(0,str(activate.LIVE))
from streamlit.testing.v1 import AppTest
ui=AppTest.from_file('/home/varma/kalshi/kalshi_unbroken_bot/dashboard.py').run(timeout=40)
assert not ui.exception,str(list(ui.exception))
rules=[]
for table in ui.dataframe:
    frame=table.value
    if {'Asset','Strategy','Minimum z','Buy cap','Contracts'}.issubset(frame.columns):rules=frame.to_dict('records')
assert len(rules)==16 and all(r['Contracts']==5 for r in rules)
cad=[r for r in rules if r['Asset']=='USDCAD'];assert len(cad)==2
assert {float(r['Buy cap'].rstrip('¢')) for r in cad}=={93.9,97.5}
assert all(r['Minutes remaining']=='1–10' for r in cad)
ui.radio(key='desk_market_filter').set_value('USD/CAD').run(timeout=40)
assert not ui.exception
filtered=[]
for table in ui.dataframe:
    frame=table.value
    if {'Asset','Strategy','Minimum z','Buy cap','Contracts'}.issubset(frame.columns):filtered=frame.to_dict('records')
assert len(filtered)==2 and all(r['Asset']=='USDCAD' for r in filtered)
backup=Path(receipt['backup'])
for table,key,filename in (('orders','ticker','ledger_records.json'),('order_attempts','client_id','attempt_records.json')):
    old=json.loads((backup/filename).read_text());current=activate.records(table,key)
    assert all(current.get(k)==v for k,v in old.items()),'Historical records changed'
s=json.loads((activate.STATE/'status.json').read_text())
assert 0<=time.time()-s['ts']<30 and s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted'))
assert activate.configs(s)==receipt['config_after']
assert s['assets']['USDCAD']['enabled'] and not s['assets']['USDCAD']['blocked']
assert s['assets']['BTC']['rules']['cushion_z2']['z_base_at_5m']==3
assert 'AUDUSD' not in s['assets'] and 'COPPER' not in s['assets']
observer=json.loads(Path('/home/varma/kalshi_execution_retry_2026_10_02/observer_state/status.json').read_text())
assert 0<=time.time()-observer['ts']<30 and not observer['error']
scans=[x for x in s['scans'] if x.get('asset')=='USDCAD']
out=dict(verified_ts=time.time(),activated=True,heartbeat_age_seconds=time.time()-s['ts'],
    config=activate.configs(s),services=services,stable_checkpoint_files=checkpoint,
    dashboard_http=200,dashboard_render_exceptions=0,dashboard_rules=rules,
    cad_rules=cad,cad_filtered_rules=filtered,cad_scans=scans,cad_attempts=s['assets']['USDCAD']['attempts'],
    preserved_market_records=receipt['preserved_records'],preserved_attempts=receipt['preserved_attempts'],
    account=s['account'],observer_heartbeat=observer['ts'])
activate.save(P/'live_verification.json',out);activate.save(P/'postflight_status.json',s)
print(json.dumps({k:out[k] for k in ('verified_ts','activated','heartbeat_age_seconds','dashboard_http','dashboard_render_exceptions','cad_attempts','cad_scans','preserved_market_records','preserved_attempts')},indent=2))
