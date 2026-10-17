"""Regression for actual NO/legacy-sell receipts and fail-closed direction guards."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import stream_transport as st
from test_stream_transport import fill, order, record

def no_fill(**kw):
    return fill(**dict(dict(side='yes',purchased_side='no',outcome_side='no',book_side='ask',
                            action='sell',yes_price_dollars='.08'),**kw))
def no_order(**kw):
    return order(**dict(dict(side='yes',outcome_side='no',book_side='ask',action='sell'),**kw))

class Direction(unittest.TestCase):
    def check(self,f=None,o=None,r=None):
        cache=st.FillState();cache.apply(no_fill() if f is None else f,101)
        return cache.confirmed(no_order() if o is None else o,record(side='no') if r is None else r),cache.last_reason
    def test_actual_legacy_no_sell_accepted(self):
        out,reason=self.check();self.assertEqual(reason,'verified');self.assertEqual(out[0]['yes_price_dollars'],'0.08')
    def test_no_buy_legacy_still_supported(self):
        self.assertIsNotNone(self.check(no_fill(action='buy',side='no'),no_order(action='buy',side='no'))[0])
    def test_unknown_legacy_action_requires_canonical_identity(self):
        self.assertIsNotNone(self.check(no_fill(action=''))[0])
        self.assertIsNone(self.check(no_fill(action='',outcome_side=None))[0])
    def test_yes_buy(self):
        self.assertIsNotNone(self.check(fill(),order(),record())[0])
    def test_canonical_yes_sale_of_no(self):
        self.assertIsNotNone(self.check(fill(action='sell',side='no'),order(action='sell',side='no'),record())[0])
    def test_wrong_order_side_book_and_route(self):
        for kw in [dict(outcome_side='yes'),dict(book_side='bid'),dict(exchange_index=0),dict(subaccount_number=1)]:
            with self.subTest(kw=kw):self.assertIsNone(self.check(o=no_order(**kw))[0])
    def test_wrong_fill_side_book_purchased_and_route(self):
        for kw in [dict(outcome_side='yes'),dict(book_side='bid'),dict(purchased_side='yes'),dict(exchange_index=0)]:
            with self.subTest(kw=kw):self.assertIsNone(self.check(f=no_fill(**kw))[0])
    def test_canonical_fields_missing_fall_back(self):
        for target in ('order','fill'):
            for key in ('outcome_side','book_side'):
                f=no_fill();o=no_order();(o if target=='order' else f).pop(key)
                with self.subTest(target=target,key=key):self.assertIsNone(self.check(f,o)[0])
    def test_action_mismatch_or_explicit_close_falls_back(self):
        for action in ('buy','sell_partial','sell_to_close','unexpected',None):
            with self.subTest(action=action):self.assertIsNone(self.check(no_fill(action=action))[0])
    def test_wrong_client_falls_back(self):
        self.assertIsNone(self.check(no_fill(client_order_id='different'))[0])
    def test_optional_client_omitted_matches_terminal_client(self):
        f=no_fill();f.pop('client_order_id');self.assertIsNotNone(self.check(f)[0])
    def test_actual_rest_receipts_without_rewriting_action(self):
        rows=json.loads((Path(__file__).resolve().parent.parent/'receipts/no_fill_fixture.json').read_text())['rows']
        self.assertEqual(len(rows),4)
        for row in rows:
            cache=st.FillState();o=row['order']
            for f in row['fills']:cache.apply(f,101)
            r=dict(ticker=o['ticker'],order_id=o['order_id'],client_id=o['client_order_id'],
                   exchange_index=o['exchange_index'],side='no')
            with self.subTest(ticker=o['ticker']):
                out=cache.confirmed(o,r)
                self.assertIsNotNone(out)
                self.assertEqual({f['action'] for f in row['fills']},{'sell'})
    def test_no_receipts_after_restart_never_proves_zero(self):
        self.assertIsNone(st.FillState().confirmed(no_order(),record(side='no')))
        self.assertIsNone(st.FillState().confirmed(no_order(status='canceled',fill_count_fp='0'),record(side='no')))

class Receipts(unittest.TestCase):
    def test_real_frame_and_terminal_match_recorded_off_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'frames.jsonl';t=st.StreamTransport(lambda:{},start=False,audit_path=p)
            try:
                t._frame(dict(type='fill',msg=no_fill()))
                out=t.confirmed_fills(no_order(),record(side='no'))
                self.assertIsNotNone(out)
            finally:t.close()
            rows=[json.loads(x) for x in p.read_text().splitlines()]
            self.assertEqual([r['kind'] for r in rows],['fill_frame','fill_verification'])
            self.assertEqual(rows[0]['frame']['msg']['action'],'sell')
            self.assertEqual(rows[1]['reason'],'verified')
            self.assertEqual(t.status()['fill_audit'],dict(written=2,dropped=0,errors=0,pending=0))
    def test_fallback_has_explicit_reason(self):
        t=st.StreamTransport(lambda:{},start=False)
        self.assertIsNone(t.confirmed_fills(no_order(),record(side='no')))
        self.assertEqual(t.status()['fill_verification_reasons'],{'incomplete_or_excess_quantity':1})
    def test_audit_disk_failure_does_not_change_receipt_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            t=st.StreamTransport(lambda:{},start=False,audit_path=Path(directory)/'missing'/'frames.jsonl')
            try:
                t._frame(dict(type='fill',msg=no_fill()))
                self.assertIsNotNone(t.confirmed_fills(no_order(),record(side='no')))
            finally:t.close()
            self.assertEqual(t.status()['fill_audit']['errors'],2)

class Provenance(unittest.TestCase):
    def test_initial_transport_survives_settlement_rest_fallback(self):
        from test_engine import Tests
        helper=Tests();helper.setUp()
        try:
            r=helper.acknowledged(fill='5')
            helper.ex.fs[0]['ws_received_ts']=1009.9
            helper.engine.reconcile();first=helper.store.get(r['ticker'])
            self.assertEqual(first['initial_fill_transport'],'websocket')
            self.assertEqual(first['initial_fill_stream_received_ts'],1009.9)
            helper.ex.fs[0].pop('ws_received_ts');helper.now=1400
            helper.engine.reconcile();last=helper.store.get(r['ticker'])
            self.assertEqual(last['fill_transport'],'rest')
            self.assertEqual(last['initial_fill_transport'],'websocket')
            self.assertEqual(last['fill_confirmed_ts'],first['fill_confirmed_ts'])
            self.assertEqual(last['filled'],'5')
        finally:helper.tearDown()

if __name__=='__main__':unittest.main()
