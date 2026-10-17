"""Post-activation verification: read-only exchange requests and live files."""
import hashlib,json,sqlite3,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
import requests
P=Path(__file__).resolve().parent
LIVE=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
sys.path.insert(0,str(LIVE))
from activate import healthy
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
r=json.loads((P/'activation_receipt.json').read_text());d=json.loads((LIVE/'state/status.json').read_text())
assert r['activated'] and healthy(d,r['verified_ts'])
assert 0<=time.time()-d['ts']<45
expected=json.loads((P/'staged_hashes.json').read_text())
assert all(hashlib.sha256((LIVE/n).read_bytes()).hexdigest()==h for n,h in expected.items())
baseline=Path('/home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28')
bh=json.loads((baseline/'manifest.json').read_text())['sha256']
assert all(hashlib.sha256((baseline/'source'/n).read_bytes()).hexdigest()==h for n,h in bh.items())
with closing(sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
 current=dict(c.execute('select ticker,record from orders'))
with closing(sqlite3.connect((Path(r['backup'])/'orders_snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
 previous=dict(c.execute('select ticker,record from orders'))
assert all(current.get(t)==raw for t,raw in previous.items()),'Historical ledger changed'
rows=[json.loads(raw) for raw in current.values()]
new=[x for x in rows if x['ticker'] not in previous]
ex=Exchange(KalshiClientV2(Credentials.load(),dry_run=True,timeout=5))
proof=[]
for row in new:
 if not row.get('order_id'):continue
 order=ex.order(row['order_id']);fills=ex.fills(row['order_id'])
 assert order['ticker']==row['ticker'] and order['client_order_id']==row['client_id']
 proof.append(dict(ticker=row['ticker'],lane=row.get('lane'),asset=row['asset'],requested=row['requested_count'],
                   ledger_status=row['status'],ledger_filled=row['filled'],exchange_status=order['status'],
                   exchange_filled=order.get('fill_count_fp'),fill_rows=len(fills),entry=row.get('entry'),fees=row.get('fees')))
services={}
for name in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service'):
 raw=subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
 services[name]=dict(x.split('=',1) for x in raw.splitlines());assert services[name]['ActiveState']=='active'
health=requests.get('http://192.168.3.136:8507/_stcore/health',timeout=5);assert health.status_code==200
start=datetime.fromtimestamp(r['verified_ts'],timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
logs=subprocess.check_output(['journalctl','--user','-u','kalshi-crypto-z2-live.service','-u','kalshi-dashboard.service','-u','kalshi-execution-observer.service','--since',start,'-p','warning','--no-pager','-o','cat'],text=True)
report=dict(verified_utc=datetime.now(timezone.utc).isoformat(),activated_utc=start,live=True,
            source_matches=len(expected),checkpoint_matches=len(bh),historical_records_preserved=len(previous),ledger_records=len(rows),
            heartbeat_age_seconds=time.time()-d['ts'],halted=d.get('halted'),error=d.get('error'),
            assets={a:dict(enabled=x['enabled'],contracts=x['contracts'],route=x['exchange_index']) for a,x in d['assets'].items()},
            limits={k:d[k] for k in ('max_contracts','max_risk','daily_loss_budget')},services=services,
            dashboard_health=health.status_code,balances=d['account']['balances'],new_order_proof=proof,
            latest_scans=[{k:x.get(k) for k in ('asset','lane','decision_ts','status','execution_status','z','required_z')} for x in d['scans']],
            warnings_since_activation=logs.strip(),exchange_access='GET_ONLY')
(P.parent/'activation_verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
