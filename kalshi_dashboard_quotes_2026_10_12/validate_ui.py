"""Render the real dashboard views and compare financial summaries to the original."""
import importlib.util
import json
from pathlib import Path
import sys
import time
from streamlit.testing.v1 import AppTest

P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
mode=sys.argv[1]
entry=P/'candidate/preview.py' if mode=='preview' else P.parent/'kalshi/kalshi_unbroken_bot/dashboard.py'
path=P/'candidate' if mode=='preview' else LIVE
sys.path.insert(0,str(LIVE));sys.path.insert(0,str(path))
app=AppTest.from_file(str(entry)).run(timeout=30)
assert not app.exception,[x.message for x in app.exception]
import dashboard_panel as panel
import dashboard_quotes as quotes
spec=importlib.util.spec_from_file_location('previous_dashboard',P/'original/dashboard_panel.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
rows=panel.read_ledger();views=[]
for group,assets in panel.GROUPS.items():
    for label,lane in panel.STRATEGIES.items():
        subset=panel.filtered(rows,assets,lane)
        assert panel.ledger_summary(subset)==old.ledger_summary(subset)
        app.radio[0].set_value(group);app.selectbox[0].set_value(label);app.run(timeout=30)
        assert not app.exception,(group,label,[x.message for x in app.exception])
        assert not app.error
        views.append((group,label))
app.radio[0].set_value('All markets');app.selectbox[0].set_value('All strategies')
app.selectbox[1].set_value('Losses');app.run(timeout=30)
frame=next(x.value for x in app.dataframe if 'Net P&L' in x.value.columns)
active=panel.filtered(rows,panel.GROUPS['All markets'])
assert len(frame)==panel.ledger_summary(active)['losses']
deadline=time.monotonic()+8
while time.monotonic()<deadline:
    snapshot=quotes.get_quotes()
    if all(x.get('checked_ts') for x in snapshot.values()):break
    time.sleep(.1)
now=time.time();quote_views={a:quotes.market_view(s,now) for a,s in snapshot.items()}
assert all(v['checked_ts'] and not v['error'] for v in quote_views.values()),quote_views
assert all(v['quote'] for v in quote_views.values() if v['current']),quote_views
app.run(timeout=30);assert not app.exception
result=dict(passed=True,mode=mode,ts=now,views=views,active_settled=panel.ledger_summary(active)['settled'],
            net=str(panel.ledger_summary(active)['net']),live_display_quotes=quote_views)
(P/('ui_'+mode+'.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(passed=True,mode=mode,views=len(views),quotes=len(quote_views),net=result['net'])))
