"""Current catalogue and read-only live EURUSD evidence; research files only."""
import json,gzip,sqlite3,hashlib,subprocess,time,re
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
from pull import P, get, END

L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
src=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db=sqlite3.connect(P/'live_snapshot.sqlite3');src.backup(db);src.close()
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def gz(n,x):
    with gzip.open(P/n,'wt') as f:json.dump(x,f)
services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True).splitlines()) for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service')}
save('preservation_before.json',dict(ts=time.time(),sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in L.glob('*.py')},flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},services=services))
status=json.loads((L/'state/status.json').read_text());save('status.json',status)
orders=[json.loads(x[0]) for x in db.execute('select record from orders')]
eur=[r for r in orders if r.get('asset')=='EURUSD'];save('actual_eurusd_orders.json',eur)
attempts=[json.loads(x[0]) for x in db.execute('select record from order_attempts')]
save('actual_eurusd_attempts.json',[r for r in attempts if r.get('asset')=='EURUSD'])
events=[]
for ident,stamp,raw in db.execute('select id,ts,event from events where ts>=? order by id',(1791427997,)):
    r=json.loads(raw)
    if r.get('asset')=='EURUSD':events.append(dict(r,_saved_ts=stamp,_id=ident))
gz('eurusd_events.json.gz',events)
obs=[]
with (P.parent/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl').open() as f:
    for line in f:
        r=json.loads(line)
        if r.get('ticker','').startswith('KXEURUSD15M'):obs.append(r)
gz('eurusd_observer.json.gz',obs)
settled=[r for r in eur if r['status']=='settled']
save('actual_summary.json',dict(trades=len(eur),settled=len(settled),wins=sum(r['side']==r['result'] for r in settled),
    losses=sum(r['side']!=r['result'] for r in settled),net=str(sum((D(r['pnl']) for r in settled),D(0))),events=len(events),observer=len(obs),
    snapshot_utc=time.time(),active_rules=status['assets']['EURUSD']['rules']))
catalog=get('/series');gz('series_catalogue.json.gz',catalog)
series=catalog['response']['series']
fx=[s for s in series if re.search(r'forex|currency|exchange rate|USDJPY|GBPUSD|EURUSD|AUDUSD|USDCAD|USDCHF|NZDUSD|EURJPY|USDCNY',s['ticker']+' '+s.get('title',''),re.I)]
save('currency_series.json',fx)
current=[]
for a in ('EURUSD','USDJPY','GBPUSD','AUDUSD','USDCAD'):
    data=get('/markets',dict(series_ticker='KX'+a+'15M',status='open',limit=100))
    save(a+'_open.json',data)
    current.append(dict(asset=a,open=len(data['response']['markets']),markets=[{k:m.get(k) for k in ('ticker','open_time','close_time','volume_fp','custom_strike','floor_strike','rules_primary','rules_secondary','exchange_index')} for m in data['response']['markets']]))
save('current_markets.json',current)
print('ACTUAL',json.loads((P/'actual_summary.json').read_text()),flush=True)
print('FXSERIES',[(s['ticker'],s.get('frequency'),s.get('title')) for s in fx],flush=True)
print('OPEN',[(r['asset'],r['open']) for r in current],flush=True)
