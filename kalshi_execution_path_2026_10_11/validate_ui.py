"""Real entrypoint render and all market/profile accounting checks."""
import sys,json,time,importlib.util
from pathlib import Path
from decimal import Decimal as D
from streamlit.testing.v1 import AppTest
P=Path(__file__).resolve().parent
mode=sys.argv[1];live=P/'candidate' if mode=='preview' else P.parent/'kalshi_revalidation_2026_09_12/live_v1'
entry=live/'preview.py' if mode=='preview' else P.parent/'kalshi/kalshi_unbroken_bot/dashboard.py'
app=AppTest.from_file(str(entry)).run(timeout=30)
assert not app.exception,[x.message for x in app.exception]
sys.path.insert(0,str(live));import dashboard_panel as panel
spec=importlib.util.spec_from_file_location('old_panel',P/'original/dashboard_panel.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
rows=panel.read_ledger();views=[]
assert 'Copper' not in app.radio[0].options
for group,assets in panel.GROUPS.items():
 for label,lane in panel.STRATEGIES.items():
  subset=panel.filtered(rows,assets,lane);assert panel.ledger_summary(subset)==old.ledger_summary(subset)
  points=panel.performance_points(subset)
  if points:assert abs(D(str(points[-1]['net']))-panel.ledger_summary(subset)['net'])<D('1e-9')
  app.radio[0].set_value(group);app.selectbox[0].set_value(label);app.run(timeout=30)
  assert not app.exception,(group,label,[x.message for x in app.exception]);assert not app.error
  views.append(dict(group=group,strategy=label,settled=panel.ledger_summary(subset)['settled']))
app.radio[0].set_value('All markets');app.selectbox[0].set_value('All strategies');app.selectbox[1].set_value('Losses');app.run(timeout=30)
frame=next(x.value for x in app.dataframe if 'Net P&L' in x.value.columns)
active=panel.filtered(rows,panel.GROUPS['All markets']);archive=panel.filtered(rows,('COPPER',))
assert len(frame)==panel.ledger_summary(active)['losses']
assert panel.ledger_summary(active)['net']+panel.ledger_summary(archive)['net']==old.ledger_summary(rows)['net']
assert any(x.label=='Archived markets · historical results' for x in app.expander)
for choice in ('Unfilled','Open / pending','Wins','All activity'):
 app.selectbox[1].set_value(choice).run(timeout=30);assert not app.exception
while str(live) in sys.path:sys.path.remove(str(live))
sys.modules.pop('operations_view',None);sys.modules.pop('lanes',None)
app.run(timeout=30);assert not app.exception
result=dict(passed=True,ts=time.time(),mode=mode,entrypoint=str(entry),views=views,archived_copper=panel.ledger_summary(archive)['settled'],active_net=str(panel.ledger_summary(active)['net']),archived_net=str(panel.ledger_summary(archive)['net']),all_net=str(old.ledger_summary(rows)['net']),fragment_imports=True)
(P/('ui_'+mode+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
