"""Read-only consistent live ledger capture; no financial writes."""
import sqlite3,json,gzip,time,sys,hashlib,subprocess,shutil
from pathlib import Path
from decimal import Decimal as D
P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
def save(n,o): (P/n).write_text(json.dumps(o,indent=2)+'\n')
def hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in LIVE.glob('*.py')}
def services():return {n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service')}
save('preservation_before.json',dict(hashes=hashes(),services=services()))
source=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True)
snapshot=sqlite3.connect(P/'audit_snapshot.sqlite3');source.backup(snapshot);source.close()
rows=[json.loads(x[0]) for x in snapshot.execute('select record from orders')]
attempts=[json.loads(x[0]) for x in snapshot.execute('select record from order_attempts')]
for n,r in [('orders',rows),('attempts',attempts)]:
 with gzip.open(P/(n+'.json.gz'),'wt') as f:json.dump(r,f)
save('status_snapshot.json',json.loads((LIVE/'state/status.json').read_text()))
(P/'production_source').mkdir(exist_ok=True)
for p in LIVE.glob('*.py'):shutil.copy2(p,P/'production_source'/p.name)
save('snapshot_receipt.json',dict(ts=time.time(),orders=len(rows),attempts=len(attempts),events=snapshot.execute('select count(*) from events').fetchone()[0],schema=list(snapshot.execute("select name,sql from sqlite_master where type='table'"))))
print(json.dumps(dict(orders=len(rows),attempts=len(attempts))),flush=True)
sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6)
original=client._request
def get_only(method,*a,**k):
 assert method=='GET'
 # Stay outside the bot's main entry/retry burst.
 while time.time()%60<32:time.sleep(1)
 time.sleep(.25)
 return original(method,*a,**k)
client._request=get_only;ex=Exchange(client)
proof=[]
for row in rows:
 if row.get('status')!='settled' or D(row['pnl'])>=0 or row.get('settled_day','')<'2026-10-08':continue
 order=ex.order(row['order_id']);fills=ex.fills(row['order_id']);market=ex.market(row['ticker'])
 assert order['ticker']==row['ticker'] and order['client_order_id']==row['client_id']
 assert market['result']==row['result']
 qty=sum(D(str(x.get('count_fp',x.get('count',0)))) for x in fills)
 cost=sum(D(str(x.get('count_fp',x.get('count',0))))*(D(x['yes_price_dollars']) if row['side']=='yes' else 1-D(x['yes_price_dollars'])) for x in fills)
 fees=sum(D(x['fee_cost']) for x in fills)
 assert qty==D(row['filled']) and cost==D(row['cost']) and fees==D(row['fees']), (row['ticker'],qty,cost,fees)
 assert qty*int(row['side']==market['result'])-cost-fees==D(row['pnl'])
 proof.append(dict(ticker=row['ticker'],ledger=row,order=order,fills=fills,market={k:market.get(k) for k in ('status','result','settlement_value','settlement_value_dollars','close_time','settlement_ts','floor_strike','expiration_value','strike_type')}))
 save('exchange_loss_proof.json',proof)
 print('Verified loss',row['ticker'],row['pnl'],flush=True)
save('exchange_health.json',dict(ts=time.time(),positions=ex.positions(),resting=ex.orders(status='resting'),cash0=str(ex.balance(0)),cash2=str(ex.balance(2))))
save('preservation_after.json',dict(hashes=hashes(),services=services()))
assert json.loads((P/'preservation_before.json').read_text())==json.loads((P/'preservation_after.json').read_text())
print('DONE',len(proof),flush=True)
