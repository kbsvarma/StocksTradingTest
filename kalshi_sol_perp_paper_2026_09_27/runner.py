"""GET-only public-data paper pilot. No keys, account client, order or transfer API."""
import argparse,datetime,fcntl,hashlib,json,os,signal as signals,sqlite3,time,urllib.request,urllib.parse
from pathlib import Path
from strategy import CONFIG,TICKER,signal,size,levels,vwap,funding_amount,pnl
ROOT=Path(__file__).resolve().parent;STATE=ROOT/'state';BASE='https://external-api.kalshi.com/trade-api/v2'
def get(path,params=None):
 allowed={'/margin/markets/'+TICKER+'/orderbook','/margin/markets/'+TICKER+'/candlesticks','/margin/funding_rates/historical','/margin/exchange/status'}
 if path not in allowed:raise ValueError('Endpoint outside public research allowlist')
 url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'GET-only-SOL-paper-pilot/1.0'},method='GET'),timeout=15) as r:return json.load(r)

def advance(st,now,book,indicator,rates,exchange_ok):
 """Pure simulation transition. Stored observations supply all inputs."""
 events=[];T=int(now//3600)*3600;pos=st.get('position');pending=st.get('pending')
 if pending and now>=pending['due']:
  if pending['kind']=='entry':
   st['pending']=None
   if now-pending['due']>120 or not exchange_ok:events.append({'type':'entry_skipped','reason':'late_or_exchange_inactive'})
   else:
    s=pending['side'];ls=levels(book,'asks' if s==1 else 'bids');bids=levels(book,'bids');asks=levels(book,'asks')
    if not bids or not asks:events.append({'type':'entry_skipped','reason':'empty_book'})
    elif (asks[0][0]-bids[0][0])/((asks[0][0]+bids[0][0])/2)*10000>20:events.append({'type':'entry_skipped','reason':'wide_book'})
    else:
     n=size(ls[0][0],pending['stop_fraction']);px=vwap(book,s,n) if n else None
     while n and px is not None and (n*px>50 or n*px*pending['stop_fraction']>1):n-=1;px=vwap(book,s,n) if n else None
     if not n or px is None:events.append({'type':'entry_skipped','reason':'size_or_depth'})
     else:
      pos=dict(side=s,count=n,entry=px,entry_ts=now,entry_scheduled_ts=pending['due'],signal_ts=pending['signal_ts'],stop_fraction=pending['stop_fraction']);st['position']=pos;events.append({'type':'paper_entry',**pos})
  elif pos and not exchange_ok:events.append({'type':'exit_waiting','reason':'exchange_inactive'})
  elif pos:
   px=vwap(book,-pos['side'],pos['count'])
   if px is None:events.append({'type':'exit_waiting','reason':'insufficient_depth'})
   else:
    f=funding_amount(pos,rates,now);calc=pnl(pos,px,f);trade={**pos,'exit':px,'exit_ts':now,'exit_reason':pending['reason'],**calc};st['trades'].append(trade);st['position']=None;st['pending']=None;pos=None;events.append({'type':'paper_exit',**trade})
 # Never manufacture historical entries on first start or backfill after outages.
 if T!=st.get('last_decision') and (indicator.get('status')=='ready' or pos):
  st['last_decision']=T;events.append({'type':'decision',**indicator})
  if pos and not st.get('pending'):
   side=pos['side'];q=vwap(book,-side,pos['count']);reason=''
   if q is not None and side*(q/pos['entry']-1)<=-pos['stop_fraction']:reason='stop'
   elif T-pos.get('entry_scheduled_ts',pos['entry_ts'])>=72*3600:reason='time'
   elif indicator.get('status')=='ready' and ((side==1 and indicator['mid']<indicator['exit_low']) or (side==-1 and indicator['mid']>indicator['exit_high'])):reason='channel'
   if reason:st['pending']={'kind':'exit','due':T+3600,'reason':reason}
  elif not pos and not st.get('pending') and T>=st['started_ts'] and now-T<=120 and exchange_ok:
   if indicator['side'] and indicator['spread_bp']<=20 and indicator['active_hour']:st['pending']={'kind':'entry','due':T+3600,'signal_ts':T,'side':indicator['side'],'stop_fraction':indicator['stop_fraction']}
 if pos:
  # Time exit still works if indicator history is unavailable.
  if T-pos.get('entry_scheduled_ts',pos['entry_ts'])>=72*3600 and not st.get('pending'):st['pending']={'kind':'exit','due':now+3600,'reason':'time_without_indicator'}
  px=vwap(book,-pos['side'],pos['count']);st['open_pnl']=pnl(pos,px,funding_amount(pos,rates,now)) if px is not None else None
 else:st['open_pnl']=None
 st['last_observation']=now;st['latest_indicator']=indicator;st['realized_pnl']=sum(t['net'] for t in st['trades']);st['exchange_active']=exchange_ok
 return events

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args();STATE.mkdir(exist_ok=True)
 lock=(STATE/'process.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 db=sqlite3.connect(STATE/'paper.sqlite3');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');db.execute('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,body TEXT)');db.execute('CREATE TABLE IF NOT EXISTS observations(ts REAL,body TEXT)');db.execute('CREATE TABLE IF NOT EXISTS events(ts REAL,body TEXT)')
 row=db.execute('SELECT body FROM state WHERE id=1').fetchone();st=json.loads(row[0]) if row else {'started_ts':time.time(),'mode':'PAPER_ONLY','position':None,'pending':None,'trades':[],'realized_pnl':0}
 digest=hashlib.sha256(json.dumps(CONFIG,sort_keys=True).encode()).hexdigest()
 if st.get('config_hash',digest)!=digest:raise RuntimeError('Frozen config differs from ledger; preserve this experiment and use a new ledger')
 st['config_hash']=digest;st['config']=CONFIG;st['code_hash']=hashlib.sha256((ROOT/'strategy.py').read_bytes()+(ROOT/'runner.py').read_bytes()).hexdigest()
 stop=[False]
 for sig in [signals.SIGTERM,signals.SIGINT]:signals.signal(sig,lambda *_:stop.__setitem__(0,True))
 cachedT=None;candles=[];rates=[];fund_at=0;ex_at=0;exchange_ok=False
 while not stop[0]:
  try:
   now=time.time();T=int(now//3600)*3600
   if cachedT!=T:
    candles=get('/margin/markets/'+TICKER+'/candlesticks',{'start_ts':T-100*3600,'end_ts':T,'period_interval':60})['candlesticks'];cachedT=T if any(c['end_period_ts']==T for c in candles) else None
   if now-fund_at>=300:
    since=min([now-7*86400]+[t['entry_ts'] for t in st['trades']]+([st['position']['entry_ts']] if st.get('position') else []));rates=get('/margin/funding_rates/historical',{'ticker':TICKER,'start_ts':int(since),'end_ts':int(now)})['funding_rates'];fund_at=now
    for trade in st['trades']:
     trade.update(pnl(trade,trade['exit'],funding_amount(trade,rates,trade['exit_ts'])))
     trade['funding_checked_ts']=now
   if now-ex_at>=60:
    x=get('/margin/exchange/status');exchange_ok=bool(x.get('exchange_active') and x.get('trading_active'));ex_at=now
   began=time.time();book=get('/margin/markets/'+TICKER+'/orderbook',{'depth':30})['orderbook'];observed=time.time()
   if observed-began>5:raise RuntimeError('Book response exceeds5seconds; wait for fresh quote')
   indicator=signal(candles,T);obs={'book':book,'indicator':indicator,'funding':rates,'funding_checked_ts':fund_at,'exchange':exchange_ok};events=advance(st,observed,book,indicator,rates,exchange_ok);st['error']=''
   db.execute('INSERT INTO observations VALUES (?,?)',(observed,json.dumps(obs)))
   for e in events:db.execute('INSERT INTO events VALUES (?,?)',(observed,json.dumps(e)))
  except Exception as e:st['error']=str(e)[:300];st['last_error_ts']=time.time();print(json.dumps({'error':st['error']}),flush=True)
  st['heartbeat']=time.time();db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(st),));db.commit();tmp=STATE/'status.tmp';tmp.write_text(json.dumps(st,indent=2));os.replace(tmp,STATE/'status.json')
  print(json.dumps({'mode':'PAPER_ONLY','heartbeat':st['heartbeat'],'indicator':st.get('latest_indicator',{}).get('status'),'position':bool(st.get('position')),'pending':st.get('pending'),'trades':len(st['trades']),'error':st.get('error')}),flush=True)
  if args.once:break
  for _ in range(30):
   if stop[0]:break
   time.sleep(1)
if __name__=='__main__':main()
