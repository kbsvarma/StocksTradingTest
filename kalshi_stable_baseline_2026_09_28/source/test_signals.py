import datetime as dt
import unittest
from unittest.mock import patch
import market_data as p

class PaperProbeTests(unittest.TestCase):
    def responses(self,T,forming=1):
        iso=lambda x:dt.datetime.fromtimestamp(x,dt.timezone.utc).isoformat().replace('+00:00','Z')
        bars=[[t,0,0,0,110 if t==T-60 else 100,1] for t in range(T-95*60,T,60)]
        bars.append([T,0,0,0,forming,1])
        return [dict(markets=[dict(ticker='TEST',open_time=iso(T-480),close_time=iso(T+420))]),
                dict(candlesticks=[dict(end_period_ts=T,yes_bid=dict(close_dollars='.89'),yes_ask=dict(close_dollars='.90'))]),
                bars,dict(orderbook_fp=dict(no_dollars=[['.10','2']],yes_dollars=[['.89','2']]))]

    def test_forming_bar_is_excluded(self):
        T=12000
        with patch.object(p,'get',side_effect=self.responses(T,forming=.01)),patch.object(p.time,'time',return_value=T+1):
            a=p.scan('BTC',T,set())
        with patch.object(p,'get',side_effect=self.responses(T,forming=99999)),patch.object(p.time,'time',return_value=T+1):
            b=p.scan('BTC',T,set())
        self.assertEqual(a,b)
        self.assertEqual(a['status'],'paper_fill')
        self.assertEqual(a['mode'],'PAPER_ONLY')

    def test_late_signal_never_paper_fills(self):
        T=12000
        with patch.object(p,'get',side_effect=self.responses(T)),patch.object(p.time,'time',return_value=T+40):
            row=p.scan('BTC',T,set())
        self.assertEqual(row['status'],'signal_too_old')

    def test_no_reentry_after_paper_fill(self):
        T=12000
        with patch.object(p,'get',side_effect=self.responses(T)),patch.object(p.time,'time',return_value=T+1):
            row=p.scan('BTC',T,{'TEST'})
        self.assertEqual(row['status'],'already_paper_entered')

    def test_unapproved_url_is_rejected(self):
        with self.assertRaises(ValueError):p.get('https://example.com/orders')

if __name__=='__main__':unittest.main()
