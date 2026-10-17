import json
from pathlib import Path
import tempfile
import time
import unittest
try:
    from streamlit.testing.v1 import AppTest
    import streamlit
    supported=tuple(map(int,streamlit.__version__.split(".")[:2])) >= (1,49)
except ImportError:
    AppTest=None
    supported=False

@unittest.skipUnless(supported,'Uses deployed Streamlit >=1.49 width=stretch support; run in server venv')
class Dashboard(unittest.TestCase):
    def test_filters_and_all_losses_use_complete_ledger(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            state=dict(ts=time.time(),mode='OBSERVE',armed=False,contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='10',scans=[])
            (root/'status.json').write_text(json.dumps(state))
            with sqlite3.connect(root/'orders.sqlite3') as conn:
                conn.execute('create table orders(ticker text,record text)')
                for i in range(120):
                    r=dict(ticker=str(i),status='settled',side='yes',result='no' if i in (0,119) else 'yes',pnl='-.96' if i in (0,119) else '.03',filled='1')
                    if i==119:r['lane']='premium_z3'
                    conn.execute('insert into orders values (?,?)',(str(i),json.dumps(r)))
            panel=Path(__file__).with_name('dashboard_panel.py')
            source=f"import importlib.util\nfrom pathlib import Path\nimport streamlit as st\nspec=importlib.util.spec_from_file_location('panel',{str(panel)!r})\np=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(p)\np.STATUS=Path({str(root/'status.json')!r})\np.render(st)"
            app=AppTest.from_string(source).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.metric[2].value,'2');self.assertEqual(app.metric[3].value,'120')
            app.radio[0].set_value('Strong cushion z3 · 5 contracts').run()
            self.assertFalse(list(app.exception));self.assertEqual(app.metric[0].value,'$-0.96');self.assertEqual(app.metric[2].value,'1')
            app.radio[0].set_value('Original z2 · 5 contracts').run()
            self.assertFalse(list(app.exception));self.assertEqual(app.metric[2].value,'1');self.assertEqual(app.metric[3].value,'119')

if __name__=='__main__':unittest.main()
