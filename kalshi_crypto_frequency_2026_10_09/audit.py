"""Snapshot and aggregate existing records; no exchange or production writes."""
import sqlite3,json,gzip,time,hashlib,subprocess,math
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';ET=ZoneInfo('America/New_York')
CRYPTO=('BTC','ETH','SOL','XRP');LANES=('cushion_z2','premium_z3')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def et(ts):return datetime.fromtimestamp(ts,ET)
def day(ts):return et(ts).date().isoformat()
def stats(vals):
 vals=sorted(vals);n=len(vals)
 return dict(n=n,median=median(vals) if n else None,p95=vals[min(n-1,int(.95*n))] if n else None,max=max(vals) if n else None)
src=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True);db=sqlite3.connect(P/'audit.sqlite3');src.backup(db);src.close()
now=time.time();status=json.loads((LIVE/'state/status.json').read_text());save('status.json',status)
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in LIVE.glob('*.py')};save('source_hashes.json',hashes)
source=P/'source';source.mkdir(exist_ok=True)
for p in LIVE.glob('*.py'):(source/p.name).write_bytes(p.read_bytes())
orders=[json.loads(r[0]) for r in db.execute('select record from orders')];attempts=[json.loads(r[0]) for r in db.execute('select record from order_attempts')]
for n,rows in [('orders',orders),('attempts',attempts)]:
 with gzip.open(P/(n+'.json.gz'),'wt') as f:json.dump(rows,f)
services={}
for n in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service'):
 services[n]=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
save('receipt.json',dict(ts=now,et=et(now).isoformat(),services=services,copper_halt=(LIVE/'state/HALT_COPPER').read_text(),db_events=db.execute('select count(*) from events').fetchone()[0]))
first={};last={};hist=defaultdict(list);perday=defaultdict(Counter);market_events=defaultdict(list);crypto_events=[]
for ts,raw in db.execute('select ts,event from events order by id'):
 e=json.loads(raw);e['_saved_ts']=ts;a=e.get('asset');lane=e.get('lane',e.get('rule','cushion_z2'));T=e.get('decision_ts')
 if a not in CRYPTO or not T:continue
 if lane not in LANES:continue
 e['lane']=lane;k=(a,lane,T);first.setdefault(k,e);last[k]=e;hist[k].append(e);market_events[e.get('ticker')].append(e)
 if T>=datetime(2026,10,3,tzinfo=ET).timestamp():crypto_events.append(e)
with gzip.open(P/'crypto_events_oct3_on.json.gz','wt') as f:json.dump(crypto_events,f)
order_map={r['ticker']:r for r in orders};daily={}
all_days=sorted(set(day(k[2]) for k in first)|set(day(x['created_ts']) for x in orders))
for d in all_days:
 out={}
 for lane in ('all',)+LANES:
  kk=[k for k in first if day(k[2])==d and (lane=='all' or k[1]==lane)]
  rr=[r for r in orders if r.get('asset') in CRYPTO and day(r['created_ts'])==d and (lane=='all' or r.get('lane','cushion_z2')==lane)]
  f=[r for r in rr if float(r.get('filled',0))>0];settled=[r for r in f if r['status']=='settled']
  all_e=[e for k in kk for e in hist[k]];sig=[];depth=[];blocked=[]
  for e in all_e:
   if 'z' in e and e['z']>=e.get('required_z',2 if e['lane']==LANES[0] else 3) and 3<=e.get('remaining_minutes',0)<=10:
    sig.append(e)
    if e.get('status') in ('eligible','paper_fill'):depth.append(e)
    if e.get('execution_status') and not e['execution_status'].startswith(('Filled','Already attempted','ack','Zero fill','Canceled')):blocked.append(e)
  sig_tickers={e['ticker'] for e in sig};depth_tickers={e['ticker'] for e in depth}
  rejected=[t for t in sig_tickers if t not in order_map or float(order_map[t].get('filled',0))==0]
  feats=[first[k] for k in kk if 'z' in first[k]]
  out[lane]=dict(entries=len(f),attempted_markets=len(rr),wins=sum(r.get('result')==r['side'] for r in settled),losses=sum(r.get('result')!=r['side'] for r in settled),net=round(sum(float(r['pnl']) for r in settled),6),contracts=sum(float(r['filled']) for r in f),partial=sum(float(r['filled'])<float(r.get('requested_count',1)) for r in f),decision_minutes=len(kk),first_status=dict(Counter(first[k].get('status') for k in kk)),final_status=dict(Counter(last[k].get('status') for k in kk)),qualified_markets=len(sig_tickers),depth_eligible_markets=len(depth_tickers),qualified_not_filled=rejected,execution_status=dict(Counter(e['execution_status'] for e in blocked)),latency_first=stats([first[k].get('observed_ts',first[k]['_saved_ts'])-k[2] for k in kk]),feature_sigma=stats([e['sigma'] for e in feats if 'sigma' in e]),feature_z=stats([e['z'] for e in feats]),by_asset=dict(Counter(r['asset'] for r in f)))
  daily[d]=out
save('daily.json',daily)
missed={}
for d,v in daily.items():
 if d<'2026-10-03':continue
 for t in v['all']['qualified_not_filled']:
  missed[t]=[e for e in market_events[t] if e.get('z',-100)>=e.get('required_z',2 if e.get('lane')==LANES[0] else 3)]
save('qualified_unfilled.json',missed)
# Exact first/last scan presence per minute; identify material outages rather than counting repeated retry rows.
coverage={}
for d in all_days:
 if d<'2026-10-03':continue
 start=datetime.fromisoformat(d).replace(tzinfo=ET).timestamp();end=min(start+86400,now);minute_keys=range(int(start),int(end//60)*60,60)
 expected={(a,lane,T) for T in minute_keys for a in CRYPTO for lane in LANES}
 missing=expected-set(first)
 coverage[d]=dict(expected=len(expected),seen=len(expected-set(missing)),missing=len(missing),missing_by_asset_lane=dict(Counter(a+'/'+l for a,l,T in missing)),missing_minutes_et=sorted({et(T).isoformat() for a,l,T in missing})[:80])
save('coverage.json',coverage)
print('SNAPSHOT',et(now).isoformat(),len(orders),len(crypto_events),flush=True)
for d,v in daily.items():
 if d>='2026-09-25':
  q=v['all'];print(d,{k:q[k] for k in ('entries','wins','losses','net','qualified_markets','depth_eligible_markets','qualified_not_filled')},flush=True)
