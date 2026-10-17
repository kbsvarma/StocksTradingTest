"""Exercise deployed entry code on copied ledger/current cash, with NO network."""
import copy,json,sys,tempfile,time
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(L))
import engine,runner,market_data,requests
from lanes import ACTIVE_ASSETS,Z2,Z3,lane_for,route_for
from test_engine import FakeExchange,signal
rows=json.loads((P/'orders.json').read_text());state=json.loads((P/'status.json').read_text())
results=[]
with patch.object(requests.sessions.Session,'request',side_effect=AssertionError('Network prohibited in offline replay')):
 for asset in ACTIVE_ASSETS:
  for lane in (Z2,Z3):
   for side in ('yes','no'):
    with tempfile.TemporaryDirectory() as tmp:
     root=Path(tmp)
     for f in (L/'state').iterdir():
      if f.name.startswith(('ARM','HALT')) and f.is_file():(root/f.name).write_bytes(f.read_bytes())
     store=engine.Store(root/'test.sqlite3')
     for r in rows:store.save(copy.deepcopy(r))
     now=int(time.time()//60)*60+3
     s=dict(signal(side,ticker='KX'+asset+'15M-OFFLINE-AUDIT'),asset=asset,lane=lane,decision_ts=now-3,close_ts=now-3+300,z=5,exchange_index=route_for(dict(asset=asset)))
     rule=lane_for(s);price=D('.96') if lane==Z3 else D('.92');s.update(signal_ask=float(price),signal_bid=float(price-D('.01')))
     ex=FakeExchange();ex.account_snapshot=lambda index:([],[],D(state['account']['balances'][str(index)]))
     ex.book=lambda ticker:dict(no_dollars=[[str(1-price),'5']],yes_dollars=[[str(1-price),'5']])
     bot=engine.Engine(ex,store,root,live=True,clock=lambda:now)
     reason=runner.attempt_with_read_retry(bot,s)
     assert reason=='ack',(asset,lane,side,reason)
     assert len(ex.posted)==1 and ex.posted[0]['requested_count']=='5'
     assert D(ex.posted[0]['favorite_limit'])<=rule.cap
     results.append(dict(asset=asset,lane=lane,side=side,simulated_result=reason,quantity=5,cap=ex.posted[0]['favorite_limit']))
     store.db.close()
(P/'offline_path_results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(dict(simulations=len(results),all_passed=True,actual_orders=0)))
