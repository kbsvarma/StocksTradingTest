"""Public GET-only prospective tennis paper observer. No brokerage credentials."""
import asyncio,collections,datetime as dt,email.utils,fcntl,hashlib,json,logging,os,re,signal,sqlite3,threading,time
from pathlib import Path
import requests
from engine import canonical,digest,plays,quote,probability,choose,book_fill,ts,fit
from store import Store
P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
ALLOWED=re.compile(r'^/(?:markets|markets/candlesticks|markets/[A-Z0-9.-]+(?:/orderbook)?|series/KX(?:ATP|WTA)MATCH|milestones|live_data/milestone/[a-f0-9-]+/game_stats)$')
LOG=logging.getLogger('tennis-paper')

class Client:
 def __init__(self,rate=4):self.local=threading.local();self.lock=threading.Lock();self.next=0.;self.rate=rate
 def get(self,path,params=None):
  if not ALLOWED.fullmatch(path):raise ValueError('Not an allowed public read: '+path)
  if not hasattr(self.local,'session'):
   self.local.session=requests.Session();self.local.session.trust_env=False
   self.local.session.headers.update({'User-Agent':'Tennis90-PaperResearch/1.0','Cache-Control':'no-cache'})
  with self.lock:
   pause=max(0,self.next-time.monotonic())
   if pause:time.sleep(pause)
   self.next=time.monotonic()+1/self.rate
  started=time.time();mono=time.monotonic();url=requests.Request('GET',BASE+path,params=params).prepare().url
  status=0;body=None;headers={};error=None
  try:
   r=self.local.session.get(url,timeout=(3,5),allow_redirects=False);status=r.status_code;headers={k:r.headers.get(k) for k in ('Date','Age','ETag','Cache-Control')}
   r.raise_for_status()
   if status!=200:raise ValueError('Unexpected HTTP status '+str(status))
   body=r.json()
  except (requests.RequestException,ValueError) as ex:error=str(ex)[:1000]
  return dict(url=url,started=started,received=time.time(),duration=time.monotonic()-mono,status=status,headers=headers,error=error,body=body)

class Observer:
 def __init__(self,root=P):
  self.root=Path(root);self.cfg=json.loads((self.root/'frozen_config.json').read_text());self.client=Client(self.cfg['rate_per_second']);self.store=Store(self.root/'paper.sqlite',self.cfg);self.db=self.store.db;self.running=True;self.workers={};self.filling=set();self.clock_ok=False;self.series={};self.last_clock=(time.time(),time.monotonic())
  assert self.cfg['paper_only'] is True and self.cfg['contracts']==1
  assert hashlib.sha256((self.root/'model.py').read_bytes()).hexdigest()==self.cfg['model_sha256']
  self.store.setmeta('boot',time.time())
 async def get(self,path,params=None):
  r=await asyncio.to_thread(self.client.get,path,params);rid,h=self.store.receipt(r);r.update(id=rid,hash=h)
  if r['error'] or r['duration']>self.cfg['max_http_seconds']:
   self.store.log('http_failure',path,r['error'] or 'slow_response');raise RuntimeError(r['error'] or 'Response took more than5s')
  return r
 async def paged(self,path,params):
  out=[];cursor='';seen=set()
  while True:
   r=await self.get(path,{**params,**({'cursor':cursor} if cursor else {})});x=r['body'];out+=x['markets'];cursor=x.get('cursor','')
   if not cursor:return out
   if cursor in seen:raise RuntimeError('Repeated pagination cursor')
   seen.add(cursor)
 async def clock(self):
  while self.running:
   wall,mono=time.time(),time.monotonic();jump=abs((wall-self.last_clock[0])-(mono-self.last_clock[1]));self.last_clock=(wall,mono)
   try:
    proc=await asyncio.create_subprocess_exec('timedatectl','show','-p','NTPSynchronized','--value',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE);stdout,_=await asyncio.wait_for(proc.communicate(),5);self.clock_ok=stdout.strip()==b'yes' and jump<2
   except Exception as ex:self.clock_ok=False;self.store.log('clock_check','',ex)
   self.store.setmeta('clock',dict(checked=time.time(),ntp_synchronized=self.clock_ok,jump_seconds=jump))
   await asyncio.sleep(30)
 async def catalogue(self):
  lastfees=0
  while self.running:
   try:
    if time.time()-self.store.meta('started')>86400*self.cfg['stop_new_entries_days']:
     self.store.setmeta('collection_horizon_reached',time.time())
     for row in self.db.execute('SELECT DISTINCT event FROM positions WHERE settled IS NULL').fetchall():
      event=row['event']
      if event not in self.workers or self.workers[event].done():self.workers[event]=asyncio.create_task(self.match(event))
     await asyncio.sleep(60);continue
    if time.time()-lastfees>3600:
     for asset in self.cfg['series']:
      r=await self.get('/series/'+asset);meta=r['body']['series'];mult=float(meta['fee_multiplier']);old=self.store.meta('fee_'+asset)
      if meta.get('fee_type') not in ('quadratic','quadratic_with_maker_fees') or mult<=0 or (old and old['mult']!=mult):
       self.store.log('fee_changed',asset,str(mult));self.series[asset]=None
      else:self.series[asset]=mult;self.store.setmeta('fee_'+asset,dict(mult=mult,fee_type=meta['fee_type'],receipt=r['id'],received=r['received']))
     lastfees=time.time()
    for asset in self.cfg['series']:
     ms=await self.paged('/markets',dict(series_ticker=asset,status='open',limit=1000));groups=collections.defaultdict(list)
     for m in ms:groups[m['event_ticker']].append(m)
     for event,items in sorted(groups.items()):
      old=self.db.execute('SELECT * FROM events WHERE event=?',(event,)).fetchone()
      if old and time.time()-old['updated']<300:continue
      r=await self.get('/milestones',dict(related_event_ticker=event,limit=100));miles=[m for m in r['body']['milestones'] if m.get('details',{}).get('main_game_event_ticker')==event]
      if len(miles)!=1:continue
      mi=miles[0]
      if str(mi['details'].get('best_of'))!='3' or len(items)!=2:continue
      start=ts(mi['start_date']);info=dict(markets=items,milestone=mi,catalogue_receipt=r['id']);now=time.time()
      with self.db:
       self.db.execute('INSERT INTO events(event,series,start,milestone,info,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(event) DO UPDATE SET start=excluded.start,info=excluded.info,updated=excluded.updated,milestone=excluded.milestone',(event,asset,start,mi['id'],canonical(info),now))
      if old and old['start']!=start:
       self.store.log('schedule_changed',event,canonical([old['start'],start]))
       if old['prior'] and not self.store.snapshot(event,now):
        with self.db:self.db.execute('UPDATE events SET prior=NULL WHERE event=?',(event,))
     self.store.setmeta('catalogue_'+asset,dict(at=time.time(),markets=len(ms),events=len(groups)))
    for row in self.db.execute('SELECT event FROM events WHERE done=0').fetchall():
     event=row['event']
     if event not in self.workers or self.workers[event].done():self.workers[event]=asyncio.create_task(self.match(event))
    self.store.setmeta('catalogue_last_success',time.time())
   except Exception as ex:LOG.exception('Catalogue failed');self.store.log('catalogue_failure','',ex)
   await asyncio.sleep(60)
 async def candles(self,markets,boundary):
  r=await self.get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in markets),start_ts=boundary-600,end_ts=boundary,period_interval=1))
  return {m['market_ticker']:{int(c['end_period_ts']):c for c in m['candlesticks']} for m in r['body'].get('markets',[])},r
 async def prior(self,row,now):
  if row['prior']:return
  start=row['start'];info=json.loads(row['info']);mi=info['milestone'];a=mi['details'].get('first_competitor_id');b=mi['details'].get('second_competitor_id');markets=info['markets'];target=next((m for m in markets if m.get('custom_strike',{}).get('tennis_competitor')==a),None)
  if not target or not a or not b:return
  for minutes in (30,5):
   boundary=int((start-minutes*60)//60)*60
   if not boundary+2<=now<=boundary+20:continue
   cs,r=await self.candles(markets,boundary);q=quote(cs.get(target['ticker'],{}).get(boundary))
   if not q or q[1]-q[0]>.1 or not .02<sum(q)/2<.98:return
   prior=dict(home=a,away=b,probability=sum(q)/2,target_t=boundary,received=r['received'],receipt=r['id'],scheduled_start=start,minutes=minutes,validated=False)
   model=fit(.65 if row['series']=='KXATPMATCH' else .60,prior['probability']);prior.update(pa=model.pa,pb=model.pb);model.clear()
   with self.db:self.db.execute('UPDATE events SET prior=? WHERE event=? AND prior IS NULL',(canonical(prior),row['event']))
   self.store.log('prior_captured',row['event'],canonical(prior));return
 async def poll_score(self,row):
  r=await self.get('/live_data/milestone/'+row['milestone']+'/game_stats');es=self.store.ingest_score(row['event'],r['id'],r['hash'],r['received'],r['started'])
  if es and row['prior']:
   p=json.loads(row['prior']);earliest=min(float(e['wall_clock']) for e in es)
   if p['received']>=earliest:
    p.update(validated=False,invalid='prior_not_received_before_first_point')
   elif not p.get('invalid'):
    home,away=es[0]['home'],es[0]['away']
    if (p['home'],p['away'])==(away,home):p.update(home=home,away=away,pa=p['pb'],pb=p['pa'],probability=1-p['probability'])
    if (p['home'],p['away'])!=(home,away):p.update(validated=False,invalid='prior_competitor_mapping')
    else:p['validated']=True
   with self.db:self.db.execute('UPDATE events SET prior=? WHERE event=?',(canonical(p),row['event']))
  return es
 async def decide(self,row,boundary):
  event=row['event'];now=time.time();data={};reason=None
  if self.db.execute('SELECT 1 FROM decisions WHERE event=? AND boundary=?',(event,boundary)).fetchone():return
  if not self.clock_ok:reason='clock_not_synchronized'
  elif (self.root/'HALT_PAPER').exists():reason='manual_paper_halt'
  elif now-self.store.meta('started')>86400*self.cfg['stop_new_entries_days']:reason='28_day_entry_horizon'
  elif row['series'] not in self.series or self.series[row['series']] is None:reason='fee_metadata_unavailable_or_changed'
  elif not row['prior'] or not json.loads(row['prior']).get('validated'):reason='no_valid_prospective_prior'
  elif self.store.meta('clock') is None or now-self.store.meta('clock')['checked']>65:reason='clock_check_stale'
  snap=self.store.snapshot(event,boundary)
  if not reason and (not snap or boundary-snap['received']>self.cfg['max_score_poll_gap']):reason='score_poll_gap'
  if not reason:
   prior=json.loads(row['prior']);es=plays(self.store.body(snap['hash']));pstate,reason=probability(es,prior,boundary,self.cfg)
   data=dict(snapshot=snap,prior=prior)
   if not reason:
    cm,r=await self.candles(json.loads(row['info'])['markets'],boundary)
    if time.time()>boundary+self.cfg['decision_deadline']:reason='decision_deadline'
    elif not all(boundary in cm.get(m['ticker'],{}) for m in json.loads(row['info'])['markets']):
     # Missing publication may be retried at the next loop, without changing boundary.
     self.store.log('candle_not_published',event,str(boundary));return
    else:
     pick,reason=choose(json.loads(row['info'])['markets'],cm,pstate,prior,boundary,self.series[row['series']],self.cfg)
     data.update(probability=pstate,candle_receipt=r['id'],candle_received=r['received'],pick=pick)
  with self.db:self.db.execute('INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?)',(event,boundary,time.time(),reason or 'eligible',canonical(data)))
  if not reason:
   pick=data['pick'];pick.update(event=event,series=row['series'],source_snapshot=snap['id'],prior_receipt=data['prior']['receipt'],candle_receipt=data['candle_receipt'])
   for scenario in ('base','stress'):
    aid,blocked=self.store.attempt(event,boundary,scenario,pick,time.time())
    if aid:asyncio.create_task(self.execute(aid))
    elif blocked:self.store.log('portfolio_skip',event,scenario+':'+blocked)
 async def match(self,event):
  lastscore=0.;lastsettle=0.;lastdec=0
  while self.running:
   row=self.db.execute('SELECT * FROM events WHERE event=?',(event,)).fetchone()
   if not row or row['done']:return
   now=time.time();info=json.loads(row['info']);active=(row['start']-120<=now<=row['start']+12*3600)
   try:
    horizon=now-self.store.meta('started')>86400*self.cfg['stop_new_entries_days']
    if horizon and not self.db.execute('SELECT 1 FROM positions WHERE event=? AND settled IS NULL',(event,)).fetchone():return
    if not horizon:await self.prior(row,now)
    active=active and not horizon
    if active and now-lastscore>=self.cfg['score_poll_seconds']:
     await self.poll_score(row);lastscore=time.time();row=self.db.execute('SELECT * FROM events WHERE event=?',(event,)).fetchone()
    boundary=int(now//60)*60
    if active and row['start']<=boundary and boundary+2<=time.time()<=boundary+self.cfg['decision_deadline'] and time.time()-lastdec>1:
     await self.decide(row,boundary);lastdec=time.time()
    # Finalized-market polling also resolves rescheduled/canceled games and
    # long suspensions; age alone never marks an open holding as settled.
    if now>=row['start'] and now-lastsettle>=120:
     final=True
     for m in info['markets']:
      r=await self.get('/markets/'+m['ticker']);latest=r['body']['market'];self.store.settle(m['ticker'],latest,r['received'],r['id'])
      if not latest.get('settlement_ts'):final=False
     if final:
      with self.db:self.db.execute('UPDATE events SET done=1 WHERE event=?',(event,))
      return
     lastsettle=time.time()
   except Exception as ex:self.store.log('match_error',event,ex);LOG.warning('%s: %s',event,ex);await asyncio.sleep(3)
   await asyncio.sleep(1 if active or row['start']-1900<=now else 5)
 async def execute(self,aid):
  if aid in self.filling:return
  self.filling.add(aid)
  try:
   a=self.db.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
   if not a or a['status']!='pending':return
   d=json.loads(a['data']);await asyncio.sleep(max(0,a['due']-time.time()))
   if time.time()>a['due']+self.cfg['book_timeout']:self.store.fail(aid,'missed_execution_deadline');return
   if not self.clock_ok:self.store.fail(aid,'clock_not_synchronized');return
   if (self.root/'HALT_PAPER').exists():self.store.fail(aid,'manual_paper_halt');return
   mr=await self.get('/markets/'+d['ticker']);m=mr['body']['market']
   if m.get('status')!='active' or m.get('settlement_ts') or ts(m['close_time'])<=time.time():self.store.fail(aid,'market_not_active');return
   samples=[]
   for phase in (0,1):
    if phase:await asyncio.sleep(self.cfg['second_book_delay'])
    r=await self.get('/markets/'+d['ticker']+'/orderbook',dict(depth=0));fill,reason=book_fill(r['body'],d['side'],d['cap'])
    if r['started']<a['due'] or r['received']>a['due']+self.cfg['book_timeout']:reason='book_deadline'
    age=r.get('headers',{}).get('Age')
    if age is not None and float(age)>1:reason='cached_book'
    with self.db:self.db.execute('INSERT INTO book_checks(attempt,phase,receipt,received,data,reason) VALUES(?,?,?,?,?,?)',(aid,phase,r['id'],r['received'],canonical(fill),reason))
    if reason:self.store.fail(aid,reason);return
    samples.append(fill)
   price=max(d['ask'],*(s['vwap'] for s in samples))
   if self.store.fill(aid,price,time.time()):LOG.info('PAPER %s %s %s %.4f',a['scenario'],d['ticker'],d['side'],price)
  except Exception as ex:self.store.fail(aid,'execution_error:'+str(ex));self.store.log('execution_error',str(aid),ex)
  finally:self.filling.discard(aid)
 async def maintenance(self):
  from reporting import write_status
  while self.running:
   self.store.setmeta('heartbeat',time.time());write_status(self.store,self.root)
   for a in self.db.execute("SELECT id FROM attempts WHERE status='pending'").fetchall():
    if a['id'] not in self.filling:asyncio.create_task(self.execute(a['id']))
   await asyncio.sleep(5)
 async def run(self):
  jobs=[asyncio.create_task(x()) for x in (self.clock,self.catalogue,self.maintenance)]
  try:await asyncio.gather(*jobs)
  finally:
   for job in jobs+list(self.workers.values()):job.cancel()
   self.db.execute('PRAGMA wal_checkpoint(PASSIVE)');self.store.setmeta('stopped',time.time())

def main():
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 lock=(P/'observer.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 from reporting import serve
 serve(P)
 asyncio.run(Observer().run())
if __name__=='__main__':main()
