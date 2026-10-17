"""Render the dashboard without starting its public quote worker."""
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from streamlit.testing.v1 import AppTest

P = Path(__file__).resolve().parent
L = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
mode = sys.argv[1]
source = P/'candidate' if mode == 'preview' else L
sys.path.insert(0, str(L))
sys.path.insert(0, str(source))
import dashboard_panel as panel
spec = importlib.util.spec_from_file_location('original_dashboard', P/'original/dashboard_panel.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
rows = panel.read_ledger()
assert 'USDCAD' not in panel.GROUPS['All markets']
assert 'USD/CAD' not in panel.GROUPS
cad = [r for r in rows if panel.record_asset(r) == 'USDCAD']
assert cad and all(r['status'] == 'settled' for r in cad)
assert panel.ledger_summary(rows) == old.ledger_summary(rows)
active = panel.filtered(rows, panel.GROUPS['All markets'])
archived = [r for r in rows if panel.record_asset(r) in panel.ARCHIVED_MARKETS]
assert len(active) + len(archived) == len(rows)
assert panel.ledger_summary(active)['net'] + panel.ledger_summary(archived)['net'] == panel.ledger_summary(rows)['net']
code = 'import streamlit as st\nimport dashboard_panel as p\np.get_quotes=lambda: {}\np.render(st)\n'
app = AppTest.from_string(code).run(timeout=30)
assert not app.exception, [e.message for e in app.exception]
assert not app.error
assert not any('USDCAD' in w.value for w in app.warning)
assert 'USD/CAD' not in app.radio[0].options
for strategy in panel.STRATEGIES:
    app.selectbox[0].set_value(strategy).run(timeout=30)
    assert not app.exception
    assert not any('USDCAD' in w.value for w in app.warning)
app = AppTest.from_string(code)
app.session_state['desk_market_filter'] = 'USD/CAD'
app.run(timeout=30)
assert not app.exception
assert app.radio[0].value == 'All markets'
result = dict(passed=True, mode=mode, active_assets=list(panel.GROUPS['All markets']),
              cad_archived_trades=len(cad), cad_net=str(panel.ledger_summary(cad)['net']),
              total_ledger_net=str(panel.ledger_summary(rows)['net']),
              active_net=str(panel.ledger_summary(active)['net']), archive_net=str(panel.ledger_summary(archived)['net']),
              full_account_risk_unchanged=panel.risk_snapshot(json.loads(panel.STATUS.read_text()), rows, 1792087200) == old.risk_snapshot(json.loads(panel.STATUS.read_text()), rows, 1792087200),
              render_exceptions=0, halted_notice_removed=True, stale_filter_recovered=True)
assert result['full_account_risk_unchanged']
(P/('verify_'+mode+'.json')).write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))
