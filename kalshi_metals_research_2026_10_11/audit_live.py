import json,sqlite3,hashlib,time,sys,gzip,subprocess
from pathlib import Path
from decimal import Decimal as D
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
source=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True);db=sqlite3.connect(P/'live_snapshot.sqlite3');source.backup(db);source.close()
rows=[json.loads(x[0]) for x in db.execute('select record from orders')]
metals=[r for r in rows if any(r['ticker'].startswith('KX'+a+'15M') for a in ('GOLD','SILVER','COPPER'))];save('actual_metal_orders.json',metals)
keys={r['ticker'] for r in metals};ev=[]
for t,raw in db.execute('select ts,event from events'):
 r=json.loads(raw)
 if r.get('ticker') in keys:ev.append(dict(_saved_ts=t,**r))
with gzip.open(P/'actual_metal_events.json.gz','wt') as f:json.dump(ev,f)
summary={}
for a in ('GOLD','SILVER','COPPER'):
 rr=[r for r in metals if r['ticker'].startswith('KX'+a+'15M') and r['status']=='settled']
 summary[a]=dict(trades=len(rr),wins=sum(r['side']==r['result'] for r in rr),losses=sum(r['side']!=r['result'] for r in rr),net=str(sum((D(r['pnl']) for r in rr),D(0))))
 for r in rr:assert D(r['pnl'])==D(r['filled'])*(r['result']==r['side'])-D(r['cost'])-D(r['fees'])
sys.path.insert(0,str(L));from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
c=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5);req=c._request

def only(method,*a,**kw):assert method=='GET';return req(method,*a,**kw)
c._request=only;e=Exchange(c);proof=[]
try:
 for r in metals:
  if r['status']=='settled' and D(r['pnl'])<0:
   market=e.market(r['ticker']);fills=e.fills(r['order_id']);order=e.order(r['order_id'])
   assert market['result']==r['result']
   proof.append(dict(ticker=r['ticker'],record=r,order=order,fills=fills,market=market))
finally:c.session.close()
save('actual_losses_verified.json',proof);save('actual_summary.json',summary)
services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service')}
save('preservation_before.json',dict(ts=time.time(),sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in L.glob('*.py')},flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},services=services))
print(json.dumps(summary));print('verified losses',[(x['ticker'],x['record']['pnl'],x['record'].get('signal',{})) for x in proof])
