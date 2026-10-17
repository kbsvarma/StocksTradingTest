"""Replay archived actual fill economics through notification guard; no live orders."""
import json,sqlite3,sys,time
from pathlib import Path
from contextlib import closing
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'candidate'))
from engine import Exchange
from kalshi_client import Credentials,KalshiClientV2
from stream_transport import FillState
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1/state'
with closing(sqlite3.connect((L/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
 rows=[json.loads(x[0]) for x in db.execute('SELECT record FROM orders')]
chosen=[]
for r in sorted(rows,key=lambda r:r.get('created_ts',0),reverse=True):
 if r.get('order_id') and r.get('status')=='settled' and r.get('asset') not in {x['asset'] for x in chosen}:
  chosen.append(r)
 if len(chosen)==8:break
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=3)
orig=client._request
def only_get(method,*args,**kw):
 assert method=='GET';return orig(method,*args,**kw)
client._request=only_get;ex=Exchange(client);out=[]
try:
 for r in chosen:
  o=ex.order(r['order_id']);fs=ex.fills(r['order_id']);cache=FillState()
  for f in fs:
   cache.apply(dict(trade_id=f['fill_id'],order_id=r['order_id'],client_order_id=r['client_id'],market_ticker=r['ticker'],exchange_index=r['exchange_index'],subaccount=0,side=r['side'],purchased_side=r['side'],action='buy',count_fp=f['count_fp'],yes_price_dollars=f['yes_price_dollars'],fee_cost=f['fee_cost']),time.time())
  proof=cache.confirmed(o,r)
  out.append(dict(ticker=r['ticker'],asset=r['asset'],order=o,fills=fs,notification_guard_matched=proof is not None,filled=r['filled'],cost=r['cost'],fees=r['fees']))
finally:client.session.close()
result=dict(mode='HISTORICAL_ECONOMICS_REPLAY_NOT_ACTUAL_WS_NOTIFICATIONS',ts=time.time(),rows=out)
(P/'receipts/historical_fill_replay.json').write_text(json.dumps(result,indent=2))
print(json.dumps([dict(asset=r['asset'],matched=r['notification_guard_matched'],filled=r['filled']) for r in out]))
