"""Deterministic original/candidate runner comparison. NO network or orders."""
import sys,json,tempfile,time,logging,statistics
from pathlib import Path
from contextlib import ExitStack
from decimal import Decimal as D
from unittest.mock import patch
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/sys.argv[1]))
import runner,engine
scenario=sys.argv[2];clock=[6000.01];target='USDCAD' if scenario=='slow_other_route' else 'BTC';seen=[];requests=[];posted=[];state=None
class StopRun(BaseException):pass
class Fake:
 def __init__(self):self.rows={}
 def spend(self,n,what):requests.append((clock[0],what));clock[0]+=n
 def account_snapshot(self,index):
  self.spend(3 if scenario=='slow_other_route' and index==2 else .06,'account:'+str(index));return [],[],D(100)
 def balance(self,exchange_index=2):self.spend(.04,'display_balance');return D(100)
 def book(self,t):self.spend(.06,'final_book');return dict(no_dollars=[['.08','5']])
 def submit(self,r):
  posted.append(dict(ts=clock[0],ticker=r['ticker'],requests=len(requests)));self.spend(.06,'POST simulated');self.rows[r['client_id']]=dict(r);return dict(order_id=r['client_id'])
 def order(self,oid):
  self.spend(.06,'order');r=self.rows[oid];return dict(order_id=oid,ticker=r['ticker'],client_order_id=r['client_id'],status='executed',initial_count_fp=r['requested_count'],fill_count_fp=r['requested_count'],remaining_count_fp='0')
 def fills(self,oid):
  self.spend(.06,'fills');r=self.rows[oid];return [dict(fill_id='f'+oid,order_id=oid,ticker=r['ticker'],count_fp=r['requested_count'],yes_price_dollars='.92',fee_cost='.01')]
 def market(self,t):raise AssertionError('unexpected settlement lookup')
 def orders(self,**kw):return []
ex=Fake()
def build_engine(*a,**kw):
 e=engine.Engine(*a,clock=lambda:clock[0],**kw)
 if scenario=='open_positions':
  for i,asset in enumerate(('ETH','SOL','XRP','NATGAS','WTI')):
   r=dict(ticker='KX'+asset+'15M-OLD',asset=asset,lane='cushion_z2',side='yes',status='open',order_id='old'+str(i),client_id='old'+str(i),close_ts=6600,requested_count='1',filled='1',ever_filled='1',cost='.92',fees='.01',favorite_limit='.93',entry='.92',signal={},attempt_number=1)
   r['terminal_fill_verified']=dict(order_id=r['order_id'],filled='1',cost='.92',fees='.01',ts=5990);e.store.save(r);ex.rows[r['order_id']]=r
 return e
def scan(asset,T,entered,lane):
 ex.spend(.08 if lane=='cushion_z2' else 0,'scan:'+asset+':'+lane);seen.append(dict(asset=asset,lane=lane,ts=clock[0]))
 if asset==target and lane=='cushion_z2':
  return dict(status='paper_fill',asset=asset,lane=lane,ticker='KX'+asset+'15M-TEST',side='yes',signal_ask=.92,signal_bid=.91,z=5,remaining_minutes=5,decision_ts=T,close_ts=T+300,exchange_index=0 if asset=='USDCAD' else 2,price_ranges=[dict(start='0',end='1',step='.001')],book_observed_ts=clock[0])
 return dict(asset=asset,lane=lane,status='price_gate',decision_ts=T)
def sleep(s):
 clock[0]+=s
 if posted or clock[0]>6032:raise StopRun()
with tempfile.TemporaryDirectory() as tmp:
 state=Path(tmp)
 for name in ('ARM_5CT','ARM_Z3_5CT','ARM_WTI_5CT','ARM_USDCAD_5CT'):(state/name).write_text('5')
 (state/'ARM_NONCRYPTO_5CT').write_text(json.dumps(dict(version=1,contracts=5,assets=['COPPER','NATGAS','EURUSD'])))
 with ExitStack() as stack:
  for name,kw in [('STATE',dict(new=state)),('legacy_safe',dict(return_value='')),('KalshiClientV2',{}),('FreshAccountReads',{}),('Exchange',dict(return_value=ex)),('Engine',dict(side_effect=build_engine))]:stack.enter_context(patch.object(runner,name,**kw))
  stack.enter_context(patch.object(runner.Credentials,'load',return_value=None));stack.enter_context(patch.object(runner.market_data,'scan',side_effect=scan));stack.enter_context(patch.object(runner.market_data,'warm_sources',return_value={}));stack.enter_context(patch.object(runner.market_data,'get'));stack.enter_context(patch.object(runner.time,'time',side_effect=lambda:clock[0]));stack.enter_context(patch.object(runner.time,'sleep',side_effect=sleep));stack.enter_context(patch.object(runner.signal,'signal'));stack.enter_context(patch('sys.argv',['runner.py','--live']));stack.enter_context(patch.object(runner.logging,'basicConfig'))
  try:runner.main()
  except StopRun:pass
 print(json.dumps(dict(version=sys.argv[1],scenario=scenario,first_scan_seconds=seen[0]['ts']-6000,target_scan_seconds=next(r['ts']-6000 for r in seen if r['asset']==target),submit_seconds=posted[0]['ts']-6000,requests_before_submit=posted[0]['requests'],network_simulated=True,actual_order=False)))
