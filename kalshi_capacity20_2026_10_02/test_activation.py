import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal as D
import engine
import market_data
import activate_capacity as activation
from test_engine import FakeExchange,signal
import test_signals


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=engine.Store(self.root/'ledger.db');self.ex=FakeExchange()
        self.bot=engine.Engine(self.ex,self.store,self.root,live=True,clock=lambda:1010)
    def tearDown(self):self.store.db.close();self.temp.cleanup()
    def test_949_signal_buys_only_at_939_or_less(self):
        for side in ('yes','no'):
            s=signal(side,ticker='KXBTC15M-'+side);s.update(signal_ask=.949,signal_bid=.939)
            self.assertEqual(engine.limit_price(s)[1],D('.939'))
            self.ex.book=lambda _:dict(yes_dollars=[['.061','5']],no_dollars=[['.061','5']])
            self.assertEqual(self.bot.attempt(s),'ack')
            r=self.store.get(s['ticker']);r['status']='unfilled';self.store.save(r)
    def test_94_cent_execution_rejected(self):
        s=signal();s.update(signal_ask=.949,signal_bid=.939)
        self.ex.book=lambda _:dict(no_dollars=[['.060','500']])
        self.assertEqual(self.bot.attempt(s),'Insufficient depth inside cap');self.assertFalse(self.ex.posted)
    def test_95_signal_boundary_rejected(self):
        s=signal();s.update(signal_ask=.95,signal_bid=.94)
        with self.assertRaisesRegex(ValueError,'signal gates'):self.bot.attempt(s)
    def test_scanner_agrees_with_engine_boundary(self):
        for ask,want in (('.949','paper_fill'),('.950','price_gate')):
            responses=test_signals.PaperProbeTests().responses(12000)
            responses[1]['candlesticks'][0]['yes_bid']['close_dollars']=str(D(ask)-D('.01'))
            responses[1]['candlesticks'][0]['yes_ask']['close_dollars']=ask
            with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=12003):
                self.assertEqual(market_data.scan('BTC',12000,set())['status'],want)
    def test_production_drift_rejected(self):
        p=self.root/'engine.py';p.write_text('original')
        hashes={'engine.py':hashlib.sha256(p.read_bytes()).hexdigest()}
        activation.verify_hashes(self.root,hashes)
        p.write_text('changed')
        with self.assertRaisesRegex(RuntimeError,'changed'):activation.verify_hashes(self.root,hashes)
    def test_manifest_cannot_overwrite_ledger(self):
        with self.assertRaisesRegex(RuntimeError,'Unexpected'):activation.verify_hashes(self.root,{'state/orders.sqlite3':'x'})
    def test_verification_requires_new_parameters_and_fresh_healthy_heartbeat(self):
        d=dict(ts=1010,mode='LIVE',armed=True,signal_ask_ceiling_dollars='0.95',execution_cap_dollars='0.939',
               contracts=5,max_contracts=20,max_risk='19.00',daily_loss_budget='10')
        self.assertTrue(activation.healthy_new_status(d,1000))
        for k,v in [('ts',900),('execution_cap_dollars','0.95'),('signal_ask_ceiling_dollars','0.93'),
                    ('halted','stop'),('error','failed'),('contracts',2),('max_contracts',8),('daily_loss_budget','20')]:
            self.assertFalse(activation.healthy_new_status(dict(d,**{k:v}),1000))
    def test_status_exposes_signal_and_execution_limits(self):
        import runner
        d=runner.status(self.bot,{},'OBSERVE')
        self.assertEqual(d['signal_ask_ceiling_dollars'],'0.95')
        self.assertEqual(d['execution_cap_dollars'],'0.939')
        self.assertEqual(d['contracts'],5)


if __name__=='__main__':unittest.main()
