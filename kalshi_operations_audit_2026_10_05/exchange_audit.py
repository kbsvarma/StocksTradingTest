"""Authenticated GET-only order/account verification. No engine or Store writes."""
import json, sqlite3, sys, time
from pathlib import Path
from decimal import Decimal as D

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'source'))
from kalshi_client import KalshiClientV2, Credentials
from engine import Exchange, crypto
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
exchange=Exchange(client)
db=sqlite3.connect((P/'snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)
rows=[json.loads(r[0]) for r in db.execute('select record from orders')]
start=json.loads((P/'z3_receipt.json').read_text())['verified_ts']
end=json.loads((P/'capture.json').read_text())['captured_ts']
orders=exchange.orders(min_ts=int(start),max_ts=int(end))
orders=[o for o in orders if crypto(o.get('ticker',''))]
local=[r for r in rows if start<=r['created_ts']<=end]
remote_ids={o['order_id'] for o in orders}
local_ids={r['order_id'] for r in local if r.get('order_id')}
selected=[r for r in local if r.get('lane')=='premium_z3' or r.get('ioc_retry_version')==1 or D(r.get('pnl','0'))<0]
details=[]
for r in selected:
    order=exchange.order(r['order_id']);fills=exchange.fills(r['order_id'])
    qty=sum((D(f['count_fp']) for f in fills),D(0))
    cost=sum((D(f['count_fp'])*(D(f['yes_price_dollars']) if r['side']=='yes' else 1-D(f['yes_price_dollars'])) for f in fills),D(0))
    fees=sum((D(f['fee_cost']) for f in fills),D(0))
    check=dict(ticker=r['ticker'],lane=r['lane'],order=order,fills=fills,
        quantity_match=qty==D(r.get('filled','0')),
        cost_match=cost==D(r.get('cost','0')),fees_match=fees==D(r.get('fees','0')))
    assert all(check[k] for k in ('quantity_match','cost_match','fees_match')),r['ticker']
    details.append(check)
out=dict(checked_ts=time.time(),GET_ONLY=True,remote_orders=len(orders),local_orders=len(local),
    remote_unmatched=sorted(remote_ids-local_ids),local_unmatched=sorted(local_ids-remote_ids),
    duplicate_client_ids=len(orders)-len({o['client_order_id'] for o in orders}),
    balance=str(exchange.balance()),positions=[r for r in exchange.positions() if crypto(r.get('ticker',''))],
    resting_orders=[r for r in exchange.orders(status='resting') if crypto(r.get('ticker',''))],details=details)
(P/'exchange_verification.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('details','positions')},indent=2))
