"""Consistent read-only same-hours sample, no production imports."""
import collections,datetime,hashlib,json,sqlite3,subprocess,time
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York');ASSETS=('BTC','ETH','SOL','XRP')
def ts(day,h,m):return int(datetime.datetime(2026,10,day,h,m,tzinfo=ET).timestamp())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def preserve():
 return dict(source={p.name:sha(p) for p in L.glob('*.py')},flags={p.name:sha(p) for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},
  services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True))
before=preserve();db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
orders=[json.loads(x[0]) for x in db.execute('select record from orders')];events=[];groups=collections.defaultdict(list)
for day in range(3,16):
 start,end=ts(day,10,55),ts(day,15,53)
 for ident,saved,raw in db.execute('select id,ts,event from events where ts>=? and ts<? order by id',(start,end+35)):
  r=json.loads(raw)
  if r.get('asset') in ASSETS and start<=r.get('decision_ts',0)<end:
   r.update(_event_id=ident,_saved_ts=saved,day=day);events.append(r);groups[(day,r['asset'],r['lane'],r['decision_ts'])].append(r)
db.close()
bad={'data_error','incomplete_spot','decision_candle_unavailable','no_decision_quote'}
def qualified(r):
 try:
  lo,hi=(.85,.95) if r['lane']=='cushion_z2' else (.93,.98)
  return lo-1e-8<=r['signal_ask']<hi-1e-8 and r['signal_ask']-r['signal_bid']<=.02000001 and 3<=r['remaining_minutes']<=10 and r['z']>=r.get('required_z',3 if r['lane']=='premium_z3' else 2)
 except (KeyError,TypeError):return False
rows=[]
for key,rs in groups.items():
 valid=[r for r in rs if r['status'] not in bad]
 r=next((r for r in rs if qualified(r)),None) or (valid[0] if valid else rs[-1])
 rows.append(dict(r,observations=len(rs),recovered=bool(valid) and any(r['status'] in bad for r in rs)))
daily=[]
for day in range(3,16):
 for asset in ASSETS:
  sub=[r for r in rows if r['day']==day and r['asset']==asset]
  qs={r['ticker'] for r in sub if qualified(r)}
  fills=[r for r in orders if r.get('asset')==asset and ts(day,10,55)<=r.get('created_ts',0)<ts(day,15,53) and float(r.get('filled',0))>0]
  daily.append(dict(day=day,asset=asset,checks=len(sub),expected=596,missing=596-len(sub),qualified=len(qs),filled=len(fills),
    categories=dict(collections.Counter(r['status'] for r in sub)),z_rows=sum(r.get('z') is not None for r in sub)))
status=json.loads((L/'state/status.json').read_text());status.pop('trades',None)
result=dict(captured=time.time(),time_window='10:55–15:52 ET inclusive',daily=daily,preservation=before,status=status)
assert preserve()==before
for name,data in [('captured.json',rows),('events.json',events),('orders.json',orders),('export.json',result)]:
 (P/name).write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(dict(rows=len(rows),events=len(events),days={day:dict(checks=sum(r['checks'] for r in daily if r['day']==day),qualified=sum(r['qualified'] for r in daily if r['day']==day),filled=sum(r['filled'] for r in daily if r['day']==day)) for day in range(3,16)}),indent=2))
