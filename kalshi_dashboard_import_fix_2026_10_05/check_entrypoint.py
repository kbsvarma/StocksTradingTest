"""Exercise the actual entry point in a clean process, without adding panel paths."""
import argparse
import json
from pathlib import Path
import sys
import time
from streamlit.testing.v1 import AppTest

p=argparse.ArgumentParser()
p.add_argument('entrypoint')
p.add_argument('--expect-broken',action='store_true')
p.add_argument('--output',required=True)
args=p.parse_args()
live=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
assert str(live) not in sys.path
assert 'operations_view' not in sys.modules
assert 'lanes' not in sys.modules
app=AppTest.from_file(args.entrypoint).run(timeout=20)
if args.expect_broken:
    errors=[e.message for e in app.exception]
    assert any("No module named 'operations_view'" in e for e in errors),errors
    result=dict(ts=time.time(),reproduced=True,errors=errors)
else:
    assert not list(app.exception),str(app.exception)
    views=[]
    for choice in ['All crypto','Original z2 · 5 contracts','Strong cushion z3 · 5 contracts']:
        app.radio[0].set_value(choice).run(timeout=20)
        assert not list(app.exception),str(app.exception)
        assert not any('diagnostics temporarily unavailable' in e.value.lower() for e in app.info)
        frames=[f.value for f in app.dataframe]
        assert any('qualified' in f.columns for f in frames),'Funnel failed to render'
        views.append(dict(view=choice,metrics={m.label:m.value for m in app.metric}))
    assert Path(sys.modules['operations_view'].__file__).parent==live
    assert Path(sys.modules['lanes'].__file__).parent==live
    # Mimic a later fragment render with the import path removed; the entry
    # point must repair its own path rather than depend on the test harness.
    while str(live) in sys.path:sys.path.remove(str(live))
    sys.modules.pop('operations_view',None);sys.modules.pop('lanes',None)
    app.run(timeout=20)
    assert not list(app.exception),str(app.exception)
    result=dict(ts=time.time(),passed=True,entrypoint=args.entrypoint,views=views,
                helper_source=sys.modules['operations_view'].__file__,path_repair_rerun=True)
Path(args.output).write_text(json.dumps(result,indent=2))
print(json.dumps(result))
