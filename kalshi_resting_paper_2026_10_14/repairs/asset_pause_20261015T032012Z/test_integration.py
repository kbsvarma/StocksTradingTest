import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
import observer
from test_paper import signal_fixture,trade_fixture,payload

class ProspectivePath(unittest.TestCase):
    def test_partial_full_and_ioc_are_distinct_with_duplicate_prints(self):
        with tempfile.TemporaryDirectory() as d:
            obj=observer.Observer.__new__(observer.Observer)
            obj.root=Path(d);obj.store=observer.Store(Path(d)/'paper.sqlite');obj.db=obj.store.db
            obj.cfg=dict(acceptance_delay_seconds=.25,cancel_latency_seconds=.25,rest_seconds=10,variants=[dict(name='rest_3c_10s',discount='.03'),dict(name='rest_1c_10s',discount='.01')],paper_cash_by_route={'0':'100','2':'100'},trade_publication_grace_seconds=15,max_book_gap_seconds=4,max_trade_pages=5)
            obj.healthy=True;obj.block='';obj.running=True;obj.fees={'BTC':dict(valid=True,multiplier=1,fee_type='quadratic',received=1000)}
            clock=[1001.0];count=[0]
            async def get(path,params=None):
                started=clock[0];clock[0]+=.1;count[0]+=1
                if path.endswith('/orderbook'):
                    body=payload(yes=[['.9','10']],no=[['.07','20']])
                else:
                    body={'cursor':'','trades':[trade_fixture(px='.90',qty='13',t=1002)] if clock[0]>1002 else []}
                r=dict(path=path,params=params,started=started,received=clock[0],status=200,body=body,error=None,headers={})
                r['id']=obj.store.receipt(r);return r
            async def sleep(seconds):clock[0]+=seconds
            obj.get=get
            with patch.object(observer.time,'time',side_effect=lambda:clock[0]),patch.object(observer.asyncio,'sleep',side_effect=sleep):
                asyncio.run(obj.opportunity(signal_fixture()))
            rows={r['variant']:r for r in obj.store.orders()}
            self.assertEqual(rows['rest_3c_10s']['filled'],'3')
            self.assertEqual(rows['rest_3c_10s']['state'],'partial')
            self.assertEqual(D(rows['rest_3c_10s']['cost']),D('2.70'))
            self.assertTrue(rows['rest_3c_10s']['coverage_complete'])
            self.assertEqual(D(rows['rest_1c_10s']['filled']),5)
            self.assertEqual(D(rows['paper_ioc']['filled']),5)
            self.assertEqual(obj.db.execute('SELECT count(*) FROM fills').fetchone()[0],2)
            obj.db.close()

if __name__=='__main__':unittest.main()
