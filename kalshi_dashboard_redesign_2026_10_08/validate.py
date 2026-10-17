"""Validate financial display invariants and the real Streamlit entry point."""
import importlib.util
import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path
from streamlit.testing.v1 import AppTest

P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
entry=Path(sys.argv[1]); output=Path(sys.argv[2])
# The wrapper must make sibling modules importable in a fresh process.
app=AppTest.from_file(str(entry)).run(timeout=30)
assert not app.exception,[x.message for x in app.exception]
assert app.radio[0].value=='All markets'
sys.path.insert(0,str(LIVE))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
panel=module('audit_panel',entry.with_name('dashboard_panel.py') if entry.name=='preview.py' else LIVE/'dashboard_panel.py')
old=module('original_panel',P/'original/dashboard_panel.py')
rows=panel.read_ledger()
views=[]
for group,assets in panel.GROUPS.items():
    for label,lane in panel.STRATEGIES.items():
        subset=panel.filtered(rows,assets,lane)
        assert panel.ledger_summary(subset)==old.ledger_summary(subset)
        points=panel.performance_points(subset)
        if points:assert abs(D(str(points[-1]['net']))-panel.ledger_summary(subset)['net'])<D('.000000001')
        app.radio[0].set_value(group);app.selectbox[0].set_value(label);app.run(timeout=30)
        assert not app.exception,(group,label,[x.message for x in app.exception])
        assert not app.error,(group,label,[x.value for x in app.error])
        views.append(dict(market=group,strategy=label,settled=panel.ledger_summary(subset)['settled']))
app.radio[0].set_value('All markets');app.selectbox[0].set_value('All strategies');app.selectbox[1].set_value('Losses');app.run(timeout=30)
assert not app.exception
frames=[x.value for x in app.dataframe]
loss_frame=next(f for f in frames if 'Net P&L' in f.columns)
assert len(loss_frame)==panel.ledger_summary(panel.read_ledger())['losses']
assert all(loss_frame['Result']=='Loss')
for choice in ('Unfilled','Open / pending','Wins','All activity'):
    app.selectbox[1].set_value(choice).run(timeout=30);assert not app.exception
now=time.time()
healthy=dict(ts=now,mode='LIVE',armed=True)
assert panel.health(healthy,now)[0]=='live'
assert panel.health(dict(healthy,ts=now-46),now)[0]=='stale'
assert panel.health(dict(healthy,halted='Manual HALT'),now)[0]=='blocked'
assert panel.health(dict(healthy,error='Network error'),now)[0]=='blocked'
assert panel.health(dict(healthy,stopped=True),now)[0]=='stopped'
assert panel.health(dict(healthy,armed=False),now)[0]=='observation'
fixture=[dict(ticker='KXBTC15M-TEST',asset='BTC',lane='cushion_z2',status='open',requested_count='5',filled='2'),
         dict(ticker='KXETH15M-TEST',asset='ETH',lane='premium_z3',status='open',requested_count='5',filled='5'),
         dict(ticker='KXSOL15M-OLD',asset='SOL',status='settled',pnl='-1',settled_day=panel.datetime.fromtimestamp(now,panel.ET).date().isoformat())]
r=panel.risk_snapshot(dict(contracts=5,daily_loss_budget='20',account={'positions':{'KXBTC15M-TEST':'2','KXXRP15M-UNKNOWN':'2'}}),fixture,now)
assert r==dict(contracts=D('12'),reserved=D('11.75'),today=D('-1'),daily_headroom=D('7.25')),r
assert panel.h('<script>')=='&lt;script&gt;'
assert panel.cash(None)=='—'
assert panel.cash(D('-.125'),True,4)=='−$0.1250'
# Exercise fragment import-path recovery rather than relying on test setup.
while str(LIVE) in sys.path:sys.path.remove(str(LIVE))
sys.modules.pop('operations_view',None);sys.modules.pop('lanes',None)
app.run(timeout=30);assert not app.exception
result=dict(passed=True,checked_ts=time.time(),entrypoint=str(entry),views=views,loss_rows=len(loss_frame),
            complete_ledger_parity=True,chart_net_parity=True,risk_partial_fill_and_external_position_case=True,
            all_health_states=True,fragment_path_recovery=True)
output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
