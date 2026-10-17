import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from streamlit.testing.v1 import AppTest
from lanes import ALL_ASSETS,CRYPTO_ASSETS,Z2,Z3

class Dashboard(unittest.TestCase):
    def test_real_import_pattern_all_groups_and_lanes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            state=dict(ts=time.time(),mode='OBSERVE',armed=False,contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='10',scans=[])
            (root/'status.json').write_text(json.dumps(state))
            with sqlite3.connect(root/'orders.sqlite3') as db:
                db.execute('create table orders(ticker text,record text)')
                for asset in ALL_ASSETS:
                    for i in range(120):
                        r=dict(ticker='KX'+asset+'15M-'+str(i),status='settled',side='yes',result='no' if i in (0,119) else 'yes',pnl='-1' if i in (0,119) else '.1',filled='5',lane=Z2 if i<119 else Z3)
                        db.execute('insert into orders values (?,?)',(r['ticker'],json.dumps(r)))
            panel=Path(__file__).with_name('dashboard_panel.py')
            source=f"import sys,importlib.util\nfrom pathlib import Path\nimport streamlit as st\nsys.path.insert(0,{str(panel.parent)!r})\nspec=importlib.util.spec_from_file_location('crypto_dashboard_panel',{str(panel)!r})\np=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(p)\np.STATUS=Path({str(root/'status.json')!r})\np.render(st)"
            app=AppTest.from_string(source).run();self.assertFalse(list(app.exception))
            for group,n in [('Crypto',4),('Copper',1),('Natural gas',1),('EUR/USD',1),('All markets',7)]:
                app.radio(key='market_group_selector').set_value(group)
                for lane,total,loss in [('All strategies',120,2),('Original lane · 5 contracts',119,1),('Premium lane · 5 contracts',1,1)]:
                    app.radio(key='crypto_lane_selector').set_value(lane).run()
                    self.assertFalse(list(app.exception));self.assertEqual(app.metric[2].value,str(n*loss));self.assertEqual(app.metric[3].value,str(n*total))

if __name__=='__main__':unittest.main()
