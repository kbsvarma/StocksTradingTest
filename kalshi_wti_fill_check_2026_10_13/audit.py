"""Read-only WTI execution forensic check; no orders, state edits or restarts."""
import collections
import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parent
LIVE = P.parent/'kalshi_revalidation_2026_09_12/live_v1'
TICKER = 'KXWTI15M-26OCT132115-15'
ET = ZoneInfo('America/New_York')
sys.path.insert(0, str(LIVE))
from kalshi_client import Credentials, KalshiClientV2
from engine import Exchange
from operations_view import qualified

def save(name, data):
    (P/name).write_text(json.dumps(data,indent=2)+'\n')
def gz(name, data):
    with gzip.open(P/name,'wt') as f: json.dump(data,f)
def et(t): return datetime.fromtimestamp(t,ET).isoformat()
before = {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in LIVE.glob('*.py')}
now = time.time()
start = datetime.fromtimestamp(now, ET).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
db = sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db.execute('BEGIN')
orders = [json.loads(r[0]) for r in db.execute('select record from orders')]
attempts = [json.loads(r[0]) for r in db.execute('select record from order_attempts')]
events=[]
for ident,saved,raw in db.execute('select id,ts,event from events where ts>=? order by id',(start,)):
    e=json.loads(raw)
    if e.get('asset')=='WTI': events.append({**e,'_id':ident,'_saved_ts':saved})
db.rollback();db.close()
wo=[o for o in orders if o.get('asset')=='WTI']
wa=[o for o in attempts if o.get('asset')=='WTI']
target=next(o for o in wo if o['ticker']==TICKER)
te=[e for e in events if e.get('ticker')==TICKER]
gz('wti_events.json.gz',events);save('wti_orders.json',wo);save('wti_attempts.json',wa)
status=json.loads((LIVE/'state/status.json').read_text());save('status.json',status)
observer=[]
op=P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl'
with op.open() as f:
    for line in f:
        if TICKER not in line: continue
        try: o=json.loads(line)
        except ValueError: continue
        if o.get('ticker')==TICKER: observer.append(o)
save('target_observer.json',observer)

client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=6)
original=client._request
requests=[]
def get_only(method,path,**kwargs):
    assert method=='GET',method
    if path.startswith('/markets') and time.time()%60<32:
        time.sleep(32-time.time()%60)
    t=time.time(); result=original(method,path,**kwargs)
    requests.append({'path':path,'received':time.time(),'seconds':time.time()-t})
    time.sleep(.25)
    return result
client._request=get_only
ex=Exchange(client)
remote=ex.order(target['order_id']);fills=ex.fills(target['order_id'])
all_target_orders=ex.orders(ticker=TICKER)
market=ex.market(TICKER)
nextfilled=next(o for o in sorted(wo,key=lambda x:x['created_ts']) if o['created_ts']>target['created_ts'] and D(o['filled'])>0)
nextorder=ex.order(nextfilled['order_id']);nextfills=ex.fills(nextfilled['order_id'])
save('exchange_receipts.json',dict(order=remote,fills=fills,all_target_orders=all_target_orders,market=market,next_order=nextorder,next_fills=nextfills,requests=requests))
assert remote['client_order_id']==target['client_id'] and remote['status']=='canceled'
assert D(remote['initial_count_fp'])==5 and D(remote['fill_count_fp'])==0 and D(remote['remaining_count_fp'])==0
assert not fills and len(all_target_orders)==1
assert sum(D(f['count_fp']) for f in nextfills)==5==D(nextorder['fill_count_fp'])
client.session.close()

cap=D(target['favorite_limit'])
def book(levels):
    asks=sorted((1-D(p),D(n)) for p,n in levels if D(n)>0)
    return {'best_ask':str(asks[0][0]) if asks else None,'depth_within_cap':str(sum((n for p,n in asks if p<=cap),D(0)))}
receipts=[]
for e in te:
    for levels,field in [('book_levels','book_observed_ts'),('execution_book_levels','execution_book_ts')]:
        if levels in e:
            receipts.append({'source':levels,'time':e[field],'et':et(e[field]),'check':e.get('execution_check_number'),**book(e[levels])})
for o in observer:
    if o.get('levels'):
        receipts.append({'source':'observer','time':o['observed_ts'],'et':et(o['observed_ts']),**book(o['levels'])})
receipts.sort(key=lambda r:r['time'])
afterzero=[r for r in receipts if target['zero_fill_confirmation']['ts']<=r['time']<=target['signal']['decision_ts']+25]
ownchecks=[e for e in te if e.get('lane')==target['lane'] and e.get('decision_ts')==target['signal']['decision_ts']]
todayorders=[o for o in wo if o['created_ts']>=start]
settled=[o for o in todayorders if o['status']=='settled']
qm={e['ticker'] for e in events if qualified(e)}
filled={o['ticker'] for o in wo if D(o.get('filled','0'))>0}
service=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
after={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in LIVE.glob('*.py')}
assert before==after
report={'captured_et':et(now),'target':target,'confirmed_exchange_status':remote['status'],'confirmed_exchange_fills':len(fills),'qualified_z':target['signal']['z'],'cap':str(cap),'execution_checks':len(ownchecks),'last_check_et':et(ownchecks[-1]['_saved_ts']),'all_book_receipts':receipts,'after_zero_inside_deadline_receipts':len(afterzero),'after_zero_full_depth_receipts':[r for r in afterzero if D(r['depth_within_cap'])>=5],'after_zero_best_ask':str(min(D(r['best_ask']) for r in afterzero if r['best_ask'] is not None)) if afterzero else None,'later_lockout_events':sum(e.get('status')=='zero_fill_locked' for e in te),'today':{'qualified_markets':len(qm),'qualified_unfilled':sorted(qm-filled),'submitted_markets':len(todayorders),'filled_markets':sum(D(o['filled'])>0 for o in todayorders),'settled':len(settled),'wins':sum(o['side']==o['result'] for o in settled),'losses':sum(o['side']!=o['result'] for o in settled),'net':str(sum((D(o['pnl']) for o in settled),D(0)))},'next_filled':{k:v for k,v in nextfilled.items() if k!='signal'},'service':service,'heartbeat_age':now-status['ts'],'wti_status':status.get('assets',{}).get('WTI'),'source_hashes':before,'protected_source_unchanged':True,'orders_submitted_by_audit':0}
save('audit.json',report)
print(json.dumps({k:v for k,v in report.items() if k not in ('target','all_book_receipts','source_hashes')},indent=2))
