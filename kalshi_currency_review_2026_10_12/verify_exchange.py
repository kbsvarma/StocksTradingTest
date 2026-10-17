"""Independently verify all EURUSD fills with authenticated GET only."""
import gzip
import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path

OUT=Path(__file__).resolve().parent;P=OUT
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
actual=json.loads((P/'actual_eurusd_orders.json').read_text())
lo=int(min(r['created_ts'] for r in actual))-60;hi=int(max(r['created_ts'] for r in actual))+1
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6)
original=client._request;calls=[]
def readonly(method,path,**kwargs):
    assert method=='GET' and (path.startswith('/portfolio/') or path=='/markets')
    phase=time.time()%60
    if phase<32:time.sleep(32-phase)
    t=time.time();response=original(method,path,**kwargs)
    calls.append(dict(path=path,ts=t,seconds=time.time()-t));time.sleep(.3)
    return response
client._request=readonly;ex=Exchange(client)
try:
    orders=ex.orders(min_ts=lo,max_ts=hi)
    fills=ex.pages(client.get_fills,'fills',subaccount=0,min_ts=lo,max_ts=hi)
    markets=ex.pages(lambda **kw:client._request('GET','/markets',params=kw),'markets',
                     series_ticker='KXEURUSD15M',min_close_ts=lo,max_close_ts=hi+900)
finally:client.session.close()
ids={r['order_id'] for r in actual};orders=[o for o in orders if o['order_id'] in ids]
fills=[f for f in fills if f['order_id'] in ids]
om={o['order_id']:o for o in orders};mm={m['ticker']:m for m in markets};proof=[]
for r in actual:
    fs=[f for f in fills if f['order_id']==r['order_id']];o=om[r['order_id']];m=mm[r['ticker']]
    qty=sum((D(f['count_fp']) for f in fs),D(0))
    cost=sum((D(f['count_fp'])*(D(f['yes_price_dollars']) if r['side']=='yes' else 1-D(f['yes_price_dollars'])) for f in fs),D(0))
    fees=sum((D(f['fee_cost']) for f in fs),D(0))
    pnl=(qty if m['result']==r['side'] else D(0))-cost-fees
    checks=dict(client=o.get('client_order_id')==r['client_id'],quantity=qty==D(r['filled'])==5,
                cost=cost==D(r['cost']),fees=fees==D(r['fees']),result=m['result']==r['result'],pnl=pnl==D(r['pnl']))
    proof.append(dict(ticker=r['ticker'],checks=checks,qty=str(qty),cost=str(cost),fees=str(fees),net=str(pnl)))
result=dict(checked_utc=time.time(),requests=calls,orders=len(orders),fills=len(fills),proof=proof,
            all_checks_pass=all(all(x['checks'].values()) for x in proof))
(OUT/'exchange_verification.json').write_text(json.dumps(result,indent=2)+'\n')
with gzip.open(OUT/'exchange_receipts.json.gz','wt') as f:json.dump(dict(orders=orders,fills=fills,markets=markets),f)
assert result['all_checks_pass']
print(json.dumps(dict(orders=len(orders),fills=len(fills),all_verified=result['all_checks_pass'],requests=len(calls))))
