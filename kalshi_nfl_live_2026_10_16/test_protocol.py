"""Offline invariant and integration tests; never use financial endpoints."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from decimal import Decimal as D
import stream_transport as st

T='KXNFLGAME-TEST-HOU'
def frame(seq=1,kind='orderbook_snapshot',**msg):
    m=dict(market_ticker=T,yes_dollars_fp=[['.91','8'],['.90','7']],no_dollars_fp=[['.92','9'],['.93','6']]) if kind=='orderbook_snapshot' else dict(market_ticker=T,side='no',price_dollars='.92',delta_fp='-2')
    m.update(msg)
    return dict(type=kind,sid=2,seq=seq,sending_ts_ms=100000,msg=m)
def fill(**kw):
    f=dict(trade_id='trade1',order_id='order1',client_order_id='client1',market_ticker=T,exchange_index=0,subaccount=0,
           side='yes',purchased_side='yes',outcome_side='yes',book_side='bid',action='buy',count_fp='5',yes_price_dollars='.92',fee_cost='.0258',ts_ms=100000)
    return dict(f,**kw)
def record(**kw):return dict(dict(order_id='order1',ticker=T,client_id='client1',exchange_index=0,side='yes'),**kw)
def order(**kw):
    return dict(dict(order_id='order1',ticker=T,client_order_id='client1',status='executed',fill_count_fp='5',remaining_count_fp='0',
      maker_fill_cost_dollars='0',taker_fill_cost_dollars='4.60',maker_fees_dollars='0',taker_fees_dollars='.0258',
      outcome_side='yes',book_side='bid',exchange_index=0,subaccount_number=0,action='buy'),**kw)

class Books(unittest.TestCase):
 def setUp(self):self.b=st.BookState();self.b.apply(frame(),100,10)
 def test_yes_coordinate_no_prices_and_highest_ten(self):
    self.assertEqual(self.b.view(T,10)['no_dollars'],[['0.07','6'],['0.08','9']])
    self.b.apply(frame(2,'orderbook_delta'),100,11)
    self.assertEqual(self.b.view(T,11)['no_dollars'],[['0.07','6'],['0.08','7']])
 def test_remove_level(self):
    self.b.apply(frame(2,'orderbook_delta',delta_fp='-9'),100,11)
    self.assertEqual(self.b.view(T,11)['no_dollars'],[['0.07','6']])
 def test_control_sequence_is_part_of_channel(self):
    self.b.apply(dict(type='ok',sid=2,seq=2),100,11)
    self.b.apply(frame(3,'orderbook_delta'),100,11)
    self.assertEqual(self.b.seq[2],3)
 def test_control_gap_invalidates_every_book(self):
    with self.assertRaises(ValueError):self.b.apply(dict(type='ok',sid=2,seq=3),100,11)
    self.assertIsNone(self.b.view(T,11))
 def test_sequence_gap_duplicate_and_reset(self):
    for seq in (1,3):
      b=st.BookState();b.apply(frame(),100,10)
      with self.assertRaises(ValueError):b.apply(frame(seq,'orderbook_delta'),100,11)
      self.assertFalse(b.books)
 def test_delta_requires_snapshot(self):
    with self.assertRaises(ValueError):st.BookState().apply(frame(1,'orderbook_delta'),100,10)
 def test_negative_quantity_invalidates(self):
    with self.assertRaises(ValueError):self.b.apply(frame(2,'orderbook_delta',delta_fp='-10'),100,11)
    self.assertFalse(self.b.books)
 def test_missing_stale_future_timestamp_invalidates(self):
    for sent in (None,98000,100300):
      f=frame();f['sending_ts_ms']=sent
      with self.assertRaises(ValueError):st.BookState().apply(f,100,10)
 def test_nonfinite_and_invalid_levels(self):
    for p,q in [('NaN','5'),('.92','Infinity'),('1.01','5'),('.92','-1')]:
      with self.assertRaises((ValueError,ArithmeticError)):st.BookState().apply(frame(yes_dollars_fp=[[p,q]]),100,10)
 def test_crossed_book_unusable(self):
    self.b.apply(frame(2,yes_dollars_fp=[['.99','1']]),100,11)
    self.assertIsNone(self.b.view(T,11))
 def test_snapshot_age_expires_and_refresh_recovers(self):
    self.assertIsNone(self.b.view(T,35.01));self.b.apply(frame(2),100,40)
    self.assertIsNotNone(self.b.view(T,40))
 def test_top10_and_empty_side(self):
    self.b.apply(frame(2,yes_dollars_fp=[[str(D(i)/100),'1'] for i in range(1,21)],no_dollars_fp=[]),100,11)
    out=self.b.view(T,11);self.assertEqual(len(out['yes_dollars']),10)
    self.assertEqual(out['yes_dollars'][0][0],'0.11');self.assertEqual(out['no_dollars'],[])
 def test_refresh_can_introduce_depth_before_its_later_removal(self):
    self.b.apply(frame(2,no_dollars_fp=[['.74','16']]),100,11)
    self.b.apply(frame(3,'orderbook_delta',price_dollars='.74',delta_fp='-16'),100,12)
    self.assertEqual(self.b.view(T,12)['no_dollars'],[])
 def test_snapshot_replaces_book(self):
    self.b.apply(frame(2,yes_dollars_fp=[],no_dollars_fp=[]),100,11)
    self.assertEqual(self.b.view(T,11)['no_dollars'],[])

class Fills(unittest.TestCase):
 def setUp(self):self.f=st.FillState();self.f.apply(fill(),101)
 def test_only_exact_terminal_economics_can_replace_rest(self):
    f=self.f.confirmed(order(),record());self.assertEqual(len(f),1);self.assertEqual(f[0]['fill_id'],'trade1')
 def test_duplicate_id_does_not_double_count(self):
    self.assertFalse(self.f.apply(fill(),102));self.assertEqual(self.f.duplicates,1)
    self.assertEqual(len(self.f.confirmed(order(),record())),1)
 def test_conflicting_duplicate_rejected(self):
    with self.assertRaises(ValueError):self.f.apply(fill(count_fp='6'),102)
 def test_zero_never_inferred_from_absence(self):
    self.assertIsNone(st.FillState().confirmed(order(status='canceled',fill_count_fp='0'),record()))
 def test_partial_before_all_receipts_falls_back(self):
    f=st.FillState();f.apply(fill(count_fp='2'),101);self.assertIsNone(f.confirmed(order(),record()))
 def test_terminal_partial_with_matching_totals_can_be_accounted_not_topped_up(self):
    f=st.FillState();f.apply(fill(count_fp='2',fee_cost='.0103'),101)
    out=f.confirmed(order(status='canceled',fill_count_fp='2',taker_fill_cost_dollars='1.84',taker_fees_dollars='.0103'),record())
    self.assertEqual(out[0]['count_fp'],'2')
 def test_nonterminal_and_remainder_fall_back(self):
    for kw in (dict(status='resting'),dict(remaining_count_fp='1')):self.assertIsNone(self.f.confirmed(order(**kw),record()))
 def test_missing_economics_falls_back(self):
    o=order();del o['maker_fees_dollars'];self.assertIsNone(self.f.confirmed(o,record()))
 def test_fee_cost_count_identity_mismatches_fall_back(self):
    for kw in (dict(taker_fees_dollars='0'),dict(taker_fill_cost_dollars='4.61'),dict(fill_count_fp='4'),dict(client_order_id='different'),dict(ticker='different')):
      self.assertIsNone(self.f.confirmed(order(**kw),record()))
 def test_route_side_action_and_client_id_mismatch_fall_back(self):
    for kw in (dict(exchange_index=2),dict(purchased_side='no'),dict(action='sell'),dict(client_order_id='other')):
      f=st.FillState();f.apply(fill(**kw),101);self.assertIsNone(f.confirmed(order(),record()))
 def test_no_side_economics(self):
    f=st.FillState();f.apply(fill(side='no',purchased_side='no',outcome_side='no',book_side='ask',yes_price_dollars='.08'),101)
    self.assertIsNotNone(f.confirmed(order(outcome_side='no',book_side='ask'),record(side='no')))
 def test_multiple_fills_complete_totals(self):
    f=st.FillState();f.apply(fill(count_fp='2',fee_cost='.0100'),101)
    f.apply(fill(trade_id='trade2',count_fp='3',fee_cost='.0158'),101)
    self.assertEqual(len(f.confirmed(order(),record())),2)
 def test_other_subaccount_ignored(self):self.assertFalse(self.f.apply(fill(subaccount=1),101))

