"""Read-only production snapshot and exchange verification. Never submits orders."""
import sqlite3,json,gzip,time,sys,hashlib,subprocess
from collections import Counter
from pathlib import Path
P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
def save(n,o): (P/n).write_text(json.dumps(o,indent=2)+'\n')
source=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True)
snapshot=sqlite3.connect(P/'audit_snapshot.sqlite3');source.backup(snapshot);source.close()
rows=[json.loads(x[0]) for x in snapshot.execute('select record from orders')]
attempts=[json.loads(x[0]) for x in snapshot.execute('select record from order_attempts')]
with gzip.open(P/'orders.json.gz','wt') as f:json.dump(rows,f)
with gzip.open(P/'attempts.json.gz','wt') as f:json.dump(attempts,f)
status=json.loads((LIVE/'state/status.json').read_text());save('status_snapshot.json',status)
save('source_hashes.json',{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in LIVE.glob('*.py')})
services={}
for name in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service'):
 services[name]=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
save('snapshot_receipt.json',dict(ts=time.time(),services=services,ledger_records=len(rows),attempts=len(attempts),event_count=snapshot.execute('select count(*) from events').fetchone()[0]))
start=1791432000 # Oct8 midnight ET
first={};final={};errors=[]
for ts,raw in snapshot.execute('select ts,event from events where ts>=? order by id',(start,)):
 e=json.loads(raw);key=(e.get('asset'),e.get('lane',e.get('rule')),e.get('ticker'),e.get('decision_ts'))
 if key not in first:first[key]=e
 final[key]=e
 if e.get('status')=='error' or e.get('execution_error'):errors.append(e)
with gzip.open(P/'decisions_today.json.gz','wt') as f:json.dump(list(final.values()),f)
save('operations.json',dict(first_counts=dict(Counter(str((k[0],v.get('status'),v.get('execution_status'))) for k,v in first.items())),final_counts=dict(Counter(str((k[0],v.get('status'),v.get('execution_status'))) for k,v in final.items())),errors=errors))
sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
original=client._request
def get_only(method,*a,**k):
 assert method=='GET'
 return original(method,*a,**k)
client._request=get_only;ex=Exchange(client)
proof=[]
for row in rows:
 if row.get('status')!='settled' or float(row['pnl'])>=0 or row.get('settled_day')!='2026-10-08':continue
 order=ex.order(row['order_id']);fills=ex.fills(row['order_id']);market=ex.market(row['ticker'])
 assert order['ticker']==row['ticker'] and order['client_order_id']==row['client_id']
 assert market['result']==row['result']
 proof.append(dict(ticker=row['ticker'],ledger_pnl=row['pnl'],ledger_fees=row['fees'],ledger_cost=row['cost'],ledger_filled=row['filled'],order=order,fills=fills,market={k:market.get(k) for k in ('status','result','settlement_value','settlement_value_dollars','close_time','settlement_ts','floor_strike','strike_type')}))
 time.sleep(.2)
save('exchange_loss_proof.json',proof)
save('exchange_health.json',dict(ts=time.time(),positions=ex.positions(),resting=ex.orders(status='resting'),cash0=str(ex.balance(0)),cash2=str(ex.balance(2))))
print(json.dumps(dict(snapshot_records=len(rows),verified_new_losses=len(proof),services=services)))
