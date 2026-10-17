"""GET-only independent account/catalogue verification; writes audit artifacts only."""
import sys,time,json,gzip,sqlite3,subprocess,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal as D
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
ET=ZoneInfo('America/New_York');START=json.loads((P/'summary.json').read_text())['last_24h']['start']
END=int(json.loads((P/'summary.json').read_text())['ts']//60)*60
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def gz(n,x):
 with gzip.open(P/n,'wt') as f:json.dump(x,f)
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6);original=client._request;calls=[]
def request(method,path,**kwargs):
 assert method=='GET' and (path.startswith('/portfolio/') or path=='/markets'),(method,path)
 sec=time.time()%60
 if sec<32:time.sleep(32-sec)
 t=time.time();r=original(method,path,**kwargs);calls.append(dict(path=path,ts=t,seconds=time.time()-t));time.sleep(.3);return r
client._request=request;ex=Exchange(client)
orders=ex.orders(min_ts=START,max_ts=END)
fills=ex.pages(client.get_fills,'fills',subaccount=0,min_ts=START,max_ts=END)
positions=ex.positions();resting=ex.orders(status='resting');balances={str(i):str(ex.balance(i)) for i in (0,2)}
gz('exchange_orders.json.gz',orders);gz('exchange_fills.json.gz',fills)
save('exchange_account.json',dict(ts=time.time(),balances=balances,positions=positions,resting=resting))
markets={}
for a in ('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI'):
 markets[a]=ex.pages(lambda **kw:client._request('GET','/markets',params=kw),'markets',series_ticker='KX'+a+'15M',min_close_ts=START,max_close_ts=END+900)
gz('markets.json.gz',markets)
with gzip.open(P/'attempts.json.gz','rt') as f:rows=[r for r in json.load(f) if START<=r['created_ts']<END]
om={o['order_id']:o for o in orders};mm={m['ticker']:m for ms in markets.values() for m in ms};proof=[]
for r in rows:
 fs=[f for f in fills if f['order_id']==r['order_id']];o=om.get(r['order_id']);m=mm.get(r['ticker'],{})
 qty=sum((D(f['count_fp']) for f in fs),D(0));cost=sum((D(f['count_fp'])*(D(f['yes_price_dollars']) if r['side']=='yes' else 1-D(f['yes_price_dollars'])) for f in fs),D(0));fee=sum((D(f['fee_cost']) for f in fs),D(0))
 checks=dict(order_found=bool(o),client_match=bool(o) and o.get('client_order_id')==r['client_id'],quantity=qty==D(r.get('filled','0')),cost=cost==D(r.get('cost','0')),fees=fee==D(r.get('fees','0')))
 if r['status']=='settled':
  pnl=(qty if m.get('result')==r['side'] else D(0))-cost-fee;checks.update(result=m.get('result')==r['result'],pnl=pnl==D(r['pnl']))
 proof.append(dict(ticker=r['ticker'],attempt=r.get('attempt_number',1),order_id=r['order_id'],exchange_status=o.get('status') if o else None,qty=str(qty),cost=str(cost),fee=str(fee),checks=checks))
save('exchange_proof.json',dict(ts=time.time(),requests=calls,proof=proof,all_checks_pass=all(all(r['checks'].values()) for r in proof)))
print('EXCHANGE',len(rows),'attempts',len(fills),'fills',len(orders),'orders','verified',all(all(r['checks'].values()) for r in proof),'markets',{a:len(m) for a,m in markets.items()},flush=True)
print('FAILURES',[r for r in proof if not all(r['checks'].values())],flush=True)
print('ACCOUNT',balances,'nonzero_positions',[(r.get('ticker'),r.get('position_fp')) for r in positions if D(str(r.get('position_fp',r.get('position',0))))!=0],'resting',len(resting),flush=True)

client.session.close()
