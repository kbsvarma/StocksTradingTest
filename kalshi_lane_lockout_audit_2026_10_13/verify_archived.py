"""GET-only archival fallback; keep initial live-endpoint 404 receipts intact."""
import gzip,json,sys,time
from decimal import Decimal as D
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'kalshi_revalidation_2026_09_12/live_v1'))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
def load(n):return json.load(gzip.open(P/(n+'.json.gz'),'rt'))
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
proof=json.loads((P/'exchange_proof.json').read_text());attempts=load('attempts');zero=[r for r in attempts if r['status']=='unfilled']
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6);orig=client._request;log=[]
def getonly(method,path,**kw):
 assert method=='GET'
 t=time.time()
 try:return orig(method,path,**kw)
 finally:log.append(dict(path=path,received=time.time(),elapsed=time.time()-t));time.sleep(.2)
client._request=getonly;ex=Exchange(client)
archived={}
for r in zero:
 t=r['ticker']
 if 'error' not in proof[t]:continue
 try:
  # Always combine tiers; don't assume fills and orders archive simultaneously.
  live_orders=ex.orders(ticker=t)
  historical_orders=ex.pages(lambda **p:client._request('GET','/historical/orders',params=p),'orders',ticker=t,subaccount=0)
  live_fills=ex.fills(r['order_id'])
  historical_fills=ex.pages(lambda **p:client._request('GET','/historical/fills',params=p),'fills',ticker=t,subaccount=0)
  matches={o['order_id']:o for o in live_orders+historical_orders if o['order_id']==r['order_id']}
  if len(matches)!=1:raise AssertionError('Missing/duplicate archived target order')
  order=next(iter(matches.values()));fills=[f for f in live_fills+historical_fills if f['order_id']==r['order_id']]
  archived[t]=dict(order=order,fills=fills,live_orders=live_orders,historical_orders=historical_orders,live_fills=live_fills,historical_fills=historical_fills)
 except Exception as e:archived[t]=dict(error=str(e)[:300])
 save('exchange_archived_proof.json',archived)
checks=[]
for r in zero:
 t=r['ticker'];p=archived.get(t,proof[t]);o=p.get('order',{})
 checks.append(dict(ticker=t,source='historical' if t in archived else 'live',error=p.get('error'),verified=(o.get('order_id')==r['order_id'] and o.get('client_order_id')==r['client_id'] and o.get('ticker')==t and o.get('status')=='canceled' and D(o.get('initial_count_fp','-1'))==D(r.get('requested_count','1')) and D(o.get('fill_count_fp','-1'))==0 and D(o.get('remaining_count_fp','-1'))==0 and not p.get('fills') and all(D(o.get(k,'-1'))==0 for k in ('maker_fill_cost_dollars','taker_fill_cost_dollars','maker_fees_dollars','taker_fees_dollars')))))
save('exchange_checks.json',checks);save('historical_request_log.json',log);client.session.close()
print(json.dumps(checks,indent=2))
