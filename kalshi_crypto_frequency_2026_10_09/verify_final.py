"""GET-only final health/proof; writes evidence only in this research folder."""
import hashlib,json,sqlite3,subprocess,sys,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal as D
import requests
P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
before=json.loads((P/'source_hashes.json').read_text())
source_ok={n:sha(LIVE/n)==h for n,h in before.items()}
checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28'
baseline=json.loads((checkpoint/'manifest.json').read_text())['sha256']
baseline_ok={n:sha(checkpoint/'source'/n)==h for n,h in baseline.items()}
receipt=json.loads((P/'receipt.json').read_text())
services={}
for n in receipt['services']:
 services[n]=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
preservation=dict(ts=time.time(),source_files=source_ok,baseline_files=baseline_ok,
 services=services,services_unchanged=services==receipt['services'],
 copper_halt_unchanged=(LIVE/'state/HALT_COPPER').read_text()==receipt['copper_halt'],production_mutations=False)
save('preservation.json',preservation)
assert all(source_ok.values()) and all(baseline_ok.values())
assert preservation['services_unchanged'] and preservation['copper_halt_unchanged']
db=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True)
rows=[json.loads(r[0]) for r in db.execute('select record from orders')];db.close()
crypto=[r for r in rows if r.get('asset') in ('BTC','ETH','SOL','XRP') and D(str(r.get('filled',0)))>0]
latest=sorted(crypto,key=lambda r:r['created_ts'])[-4:]
sys.path.insert(0,str(LIVE))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=5)
request=client._request
calls=[]
def get_only(method,*args,**kwargs):
 assert method=='GET'
 # Avoid the live bot's first 30 seconds of each decision minute.
 sec=time.time()%60
 if sec<32:time.sleep(32-sec)
 calls.append(dict(method=method,path=args[0],ts=time.time()))
 response=request(method,*args,**kwargs);time.sleep(.25);return response
client._request=get_only;ex=Exchange(client)
cached=json.loads((P/'exchange_fill_proof.json').read_text()) if '--reuse-proof' in sys.argv else None
if cached:
 assert {r['ticker'] for r in cached['proof']}=={r['ticker'] for r in latest}
proof=cached['proof'] if cached else []
for r in ([] if cached else latest):
 fills=ex.fills(r['order_id']);market=ex.market(r['ticker'])
 qty=sum((D(f['count_fp']) for f in fills),D(0))
 cost=sum((D(f['count_fp'])*(D(f['yes_price_dollars']) if r['side']=='yes' else 1-D(f['yes_price_dollars'])) for f in fills),D(0))
 fees=sum((D(f['fee_cost']) for f in fills),D(0))
 pnl=(qty if market['result']==r['side'] else D(0))-cost-fees
 checks=dict(quantity=qty==D(r['filled']),cost=cost==D(r['cost']),fees=fees==D(r['fees']),result=market['result']==r['result'],pnl=pnl==D(r['pnl']))
 proof.append(dict(ticker=r['ticker'],created_et=datetime.fromtimestamp(r['created_ts'],ET).isoformat(),side=r['side'],result=market['result'],filled=str(qty),cost=str(cost),fees=str(fees),pnl=str(pnl),checks=checks,fills=fills))
if not cached:save('exchange_fill_proof.json',dict(ts=time.time(),requests=calls,proof=proof))
assert all(all(r['checks'].values()) for r in proof)
s=json.loads((LIVE/'state/status.json').read_text());now=time.time()
today=datetime.now(ET).date().isoformat()
rr=[r for r in crypto if datetime.fromtimestamp(r['created_ts'],ET).date().isoformat()==today]
settled=[r for r in rr if r['status']=='settled']
health=dict(et=datetime.fromtimestamp(now,ET).isoformat(),heartbeat_age=now-s['ts'],
 mode=s['mode'],armed=s['armed'],stopped=s['stopped'],halted=s['halted'],error=s['error'],
 limits={k:s[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget')},
 assets={a:{k:v[k] for k in ('enabled','blocked')} for a,v in s['assets'].items()},
 account=s['account'],active=s['active'],services=services,
 ui_health_http=requests.get('http://192.168.3.136:8507/_stcore/health',timeout=5).status_code,
 crypto_today=dict(entries=len(rr),settled=len(settled),wins=sum(r['side']==r['result'] for r in settled),losses=sum(r['side']!=r['result'] for r in settled),net=str(sum((D(r['pnl']) for r in settled),D(0)))),
 latest_crypto=[{k:r.get(k) for k in ('ticker','asset','created_ts','status','filled','pnl')} for r in latest])
save('final_health.json',health)
print(json.dumps({k:v for k,v in health.items() if k not in ('account','active','latest_crypto')},indent=2))
