"""Read-only capture, isolated research outputs; public GETs only."""
from pathlib import Path
import datetime as dt,gzip,hashlib,json,sqlite3,subprocess,time,requests,pickle
P=Path(__file__).resolve().parent;ROOT=P.parent
L=ROOT/'kalshi_revalidation_2026_09_12/live_v1'
START=dt.datetime.fromisoformat('2026-10-08T00:00:00-04:00').timestamp()
END=dt.datetime.fromisoformat('2026-10-14T20:00:00-04:00').timestamp()
ASSETS={'BTC','ETH','SOL','XRP','NATGAS','WTI','EURUSD','USDCAD'}
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def preservation():
 services=['kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service']
 return dict(ts=time.time(),hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in L.iterdir() if p.suffix in ('.py','.css')},flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))},services={s:subprocess.check_output(['systemctl','--user','show',s,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for s in services})
def main():
 save('preservation_before.json',preservation())
 c=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 rows=[json.loads(r[0]) for r in c.execute('select record from orders')]
 rows=[r for r in rows if r.get('asset') in ASSETS and r['status']=='settled' and START<=r['close_ts']<=END and float(r.get('filled',0))>0]
 c.rollback();c.close();save('actual_orders.json',rows)
 known={m['ticker'] for name in ('recent_features.pickle','noncrypto_features.pickle') for m in pickle.loads((ROOT/'kalshi_loss_review_2026_10_14'/name).read_bytes())}
 fresh=[r for r in rows if r['ticker'] not in known]
 (P/'fresh').mkdir(exist_ok=True);session=requests.Session();base='https://api.elections.kalshi.com/trade-api/v2'
 def get(name,path,params=None):
  dest=P/'fresh'/name
  if dest.exists():return json.loads(dest.read_text())['response']
  time.sleep(1.5);t=time.time();res=session.get(base+path,params=params,timeout=10);res.raise_for_status();data=res.json()
  dest.write_text(json.dumps(dict(url=res.url,started=t,received=time.time(),response=data))+'\n');return data
 for r in fresh:
  t=r['ticker'];a=r['asset'];m=get(t+'_market.json','/markets/'+t)['market']
  get(t+'_candles.json','/series/KX'+a+'15M/markets/'+t+'/candlesticks',dict(start_ts=r['close_ts']-900,end_ts=r['close_ts'],period_interval=1))
  get(m['event_ticker']+'_chart.json','/live_data/events/'+m['event_ticker'],dict(range='1h'))
  prior=get(t+'_prior.json','/markets',dict(series_ticker='KX'+a+'15M',status='settled',max_close_ts=r['close_ts']-901,limit=1))['markets']
  if prior:get(prior[0]['event_ticker']+'_chart.json','/live_data/events/'+prior[0]['event_ticker'],dict(range='1h'))
  print('captured',t,flush=True)
 save('capture_receipt.json',dict(ts=time.time(),start=START,end=END,actual_entries=len(rows),fresh_markets=len(fresh),readonly=True))
 save('preservation_after_capture.json',preservation())
if __name__=='__main__':main()
