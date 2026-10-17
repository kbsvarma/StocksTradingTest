"""Render staged UI from read-only receipts and prove production is untouched."""
import json
from pathlib import Path
import sqlite3
import sys
import time
from contextlib import closing
import activate

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import runner
import dashboard_quotes
from streamlit.testing.v1 import AppTest

before=json.loads((P/'deployment_preflight.json').read_text())
expected=json.loads((P/'original_hashes.json').read_text())
checkpoint=activate.validate_hashes(expected)
assert activate.flags()==before['flags'] and not (activate.STATE/activate.ARM).exists()
services={n:activate.service(n) for n in activate.RESTART+activate.PRESERVE}
assert services==before['services'],'Services changed while preparing'
status=json.loads((activate.STATE/'status.json').read_text())
assert time.time()-status['ts']<30 and status['mode']=='LIVE' and not status['halted'] and not status['error']
assert activate.configs(status)==before['config_before']
runner.STATE=activate.STATE
class Healthy:
    def halt_reason(self):return ''
status['assets']=runner.asset_status(Healthy(),[],'LIVE')
assert status['assets']['USDCAD']['enabled'] is False
preview=P/'preview';preview.mkdir(exist_ok=True)
(preview/'status.json').write_text(json.dumps(status))
with closing(sqlite3.connect((activate.STATE/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
    rows=[json.loads(r[0]) for r in db.execute('SELECT record FROM orders')]
(preview/'ledger.json').write_text(json.dumps(rows))
source=json.loads((P/'source_probe.json').read_text())
feed={'USDCAD':dict(markets=dashboard_quotes.normalize('USDCAD',[source['market']],source['ts']),checked_ts=source['ts'],error='')}
(preview/'quotes.json').write_text(json.dumps(feed))
app='''
import json,sys
from pathlib import Path
import streamlit as st
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import dashboard_panel as panel
panel.STATUS=P/'preview/status.json'
panel.read_ledger=lambda:json.loads((P/'preview/ledger.json').read_text())
panel.read_attempts=lambda:[]
panel.get_quotes=lambda:json.loads((P/'preview/quotes.json').read_text())
panel.render(st)
'''
(P/'preview_app.py').write_text(app)
ui=AppTest.from_file(str(P/'preview_app.py')).run(timeout=40)
assert not ui.exception,str(list(ui.exception))
rules=[]
for table in ui.dataframe:
    frame=table.value
    if {'Asset','Strategy','Minimum z','Buy cap','Contracts'}.issubset(frame.columns):rules=frame.to_dict('records')
assert len(rules)==16 and all(r['Contracts']==5 for r in rules)
assert next(r for r in rules if r['Asset']=='BTC' and r['Strategy']=='Original')['Minimum z']=='3'
cad=[r for r in rules if r['Asset']=='USDCAD'];assert len(cad)==2
assert {float(r['Buy cap'].rstrip('¢')) for r in cad}=={93.9,97.5}
assert all(r['Minutes remaining']=='1–10' for r in cad)
assert all('0.5 × log₂(t / 5)' in r['Minimum z'] for r in cad)
ui.radio(key='desk_market_filter').set_value('USD/CAD').run(timeout=40)
assert not ui.exception
filtered=[]
for table in ui.dataframe:
    frame=table.value
    if {'Asset','Strategy','Minimum z','Buy cap','Contracts'}.issubset(frame.columns):filtered=frame.to_dict('records')
assert len(filtered)==2 and all(r['Asset']=='USDCAD' for r in filtered)
assert not (activate.STATE/activate.ARM).exists()
activate.validate_hashes(expected);assert activate.flags()==before['flags']
assert services=={n:activate.service(n) for n in activate.RESTART+activate.PRESERVE}
out=dict(ts=time.time(),prepared=True,activated=False,production_source_unchanged=True,
    production_flags_unchanged=True,service_pids_unchanged=True,stable_checkpoint_files=checkpoint,
    dashboard_render_exceptions=0,all_rules=len(rules),cad_rules=cad,filtered_rules=filtered,
    existing_btc_original_z=3,weather_service=services['kalshi-weather-early-paper.service'],
    live_assets=list(before['config_before']['assets']))
activate.save(P/'prepared_verification.json',out)
print(json.dumps(out,indent=2))
