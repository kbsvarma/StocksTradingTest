"""Separate NFL verifier; real entries require a verified, explicit arming token."""
import argparse,asyncio,collections,datetime as dt,fcntl,gzip,hashlib,json,os,queue,signal,subprocess,threading,time
from pathlib import Path
import requests
from core import canonical,digest,ts,parse_plays,identity,state_at,leader,current_leader,choose,depth,dec,percentile,order_payload
from store import Store
from kalshi_client import Credentials
from stream_transport import make_stream
P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'

class Reader:
    def __init__(self):self.local=threading.local();self.lock=threading.Lock();self.next={};self.stats=collections.Counter()
    def get(self,url,params=None):
        assert url.startswith((BASE+'/markets',BASE+'/series',BASE+'/events','https://site.api.espn.com/apis/site/v2/sports/football/nfl/')),'unapproved URL'
        if not hasattr(self.local,'session'):
            self.local.session=requests.Session();self.local.session.trust_env=False
            self.local.session.headers.update({'User-Agent':'NFL-Weekend-Verification/1.0','Cache-Control':'no-cache'})
        key='espn' if 'espn.com' in url else 'kalshi'
        with self.lock:
            slot=max(time.monotonic(),self.next.get(key,0));pause=slot-time.monotonic()
            self.next[key]=slot+(0.2 if key=='espn' else .5)
        # Never hold a shared lock while sleeping: book probes cannot delay ESPN.
        if pause>0:time.sleep(pause)
        start=time.time();mono=time.monotonic();body=None;status=0;headers={};error=None
        try:
            r=self.local.session.get(url,params=params,timeout=(1,2),allow_redirects=False)
            status=r.status_code;headers={k:r.headers.get(k) for k in ('Date','Age','Cache-Control','ETag')};r.raise_for_status()
            if status!=200:raise ValueError('Unexpected HTTP response')
            body=r.json()
        except Exception as ex:error=type(ex).__name__+': '+str(ex)[:220]
        self.stats[str(status)]+=1
        return dict(started=start,received=time.time(),duration=time.monotonic()-mono,status=status,headers=headers,error=error,body=body)

class Observer:
    def __init__(self,root=P,canary=False):
        self.root=Path(root);self.cfg=json.loads((self.root/'config.json').read_text());assert self.cfg['mode']=='VERIFY_ONLY' and self.cfg['contracts']==5 and not self.cfg['auto_activate']
        self.store=Store(self.root/'nfl.sqlite',self.cfg);self.db=self.store.db;self.reader=Reader();self.games={};self.running=True;self.canary=canary
        self.works={};self.filling=set();self.ntp=False;self.clock_checked=0;self.last_clock=(time.time(),time.monotonic());self.frames=queue.Queue(maxsize=50000);self.drops=0
        self.stream=make_stream(Credentials.load(),on_frame=self.frame);self.lastboot=time.time();self.lastframehash={};self.price_samples=collections.defaultdict(list)
        # A restart cannot turn an expired simulated order into a later fill.
        for r in self.db.execute("SELECT id FROM orders WHERE status='pending' AND mode='verify'").fetchall():self.store.fail(r['id'],'restart_before_simulated_execution')
        self.store.setmeta('boot',self.lastboot)
        self.frame_stop=threading.Event();self.frame_errors=0;self.frame_count=0
        self.frame_writer=threading.Thread(target=self.write_frames,daemon=True);self.frame_writer.start()
        from executor import Executor
        self.last_report={};self.executor=Executor(self)
    def write_frames(self):
        # Raw frames are journaled off the decision loop, without per-frame SQL/fsync.
        path=self.root/('frames_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.jsonl.gz')
        try:
            with gzip.open(path,'at',compresslevel=1) as out:
                last=time.monotonic()
                while not self.frame_stop.is_set() or not self.frames.empty():
                    try:r,wall=self.frames.get(timeout=.5)
                    except queue.Empty:continue
                    out.write(canonical(dict(received=wall,frame=r))+'\n');self.frame_count+=1
                    if time.monotonic()-last>1:out.flush();last=time.monotonic()
        except Exception:self.frame_errors+=1
    def frame(self,r,wall):
        if r.get('type')=='fill' and not r.get('msg',{}).get('market_ticker','').startswith('KXNFLGAME-'):return
        try:self.frames.put_nowait((r,wall))
        except queue.Full:self.drops+=1
    async def get(self,event,kind,url,params=None):
        r=await asyncio.to_thread(self.reader.get,url,params)
        if kind!='score':r['receipt']=self.store.receipt(event,kind,r)
        if r['error']:raise RuntimeError(r['error'])
        if r['duration']>3:raise RuntimeError('read_deadline')
        return r
    async def clock(self):
        while self.running:
            wall,mono=time.time(),time.monotonic();jump=abs((wall-self.last_clock[0])-(mono-self.last_clock[1]));self.last_clock=(wall,mono)
            try:
                p=await asyncio.create_subprocess_exec('timedatectl','show','-p','NTPSynchronized','--value',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                out,_=await asyncio.wait_for(p.communicate(),3);self.ntp=out.strip()==b'yes' and jump<.25
            except Exception:self.ntp=False
            self.clock_checked=wall;self.store.setmeta('clock',dict(at=wall,ntp=self.ntp,jump=jump));await asyncio.sleep(20)
    async def catalogue(self):
        while self.running:
            try:
                markets=[];cursor='';seen=set()
                while True:
                    r=await self.get('','catalogue',BASE+'/markets',dict(series_ticker='KXNFLGAME',status='open',limit=1000,**({'cursor':cursor} if cursor else {})))
                    markets+=r['body']['markets'];cursor=r['body'].get('cursor','')
                    if not cursor:break
                    if cursor in seen:raise ValueError('repeated cursor')
                    seen.add(cursor)
                series=(await self.get('','fees',BASE+'/series/KXNFLGAME'))['body']['series']
                if series.get('fee_type') not in ('quadratic','quadratic_with_maker_fees') or dec(series.get('fee_multiplier'))!=1:raise ValueError('fee schedule changed')
                changes=(await self.get('','fee_changes',BASE+'/series/fee_changes',{'series_ticker':'KXNFLGAME'}))['body']
                if changes.get('series_fee_change_arr')!=[]:raise ValueError('fee changes or invalid fee metadata require review')
                self.store.setmeta('fees',dict(at=time.time(),series=series,changes=changes))
                groups=collections.defaultdict(list)
                for m in markets:groups[m['event_ticker']].append(m)
                for date in ('20261018','20261019'):
                    r=await self.get('','schedule','https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',{'dates':date,'limit':100})
                    for g in r['body'].get('events',[]):
                        comp=g['competitions'][0];canon=lambda x:{'JAC':'JAX','WSH':'WAS','LA':'LAR'}.get(x,x)
                        teams={p['homeAway']:canon(p['team']['abbreviation']) for p in comp['competitors']}
                        matches=[(ev,ms) for ev,ms in groups.items() if ev.startswith('KXNFLGAME-26OCT'+date[-2:]) and len(ms)==2 and {canon(m['ticker'].rsplit('-',1)[1]) for m in ms}==set(teams.values())]
                        if len(matches)!=1:continue
                        ev,ms=matches[0];old=self.games.get(ev)
                        game=dict(event=ev,espn_id=g['id'],name=g['name'],start=ts(g['date']),teams=teams,markets=ms,catalogued=time.time(),done=g['status']['type'].get('completed',False))
                        if old and old['start']!=game['start']:self.store.log('schedule_changed',ev,[old['start'],game['start']])
                        self.games[ev]=game
                        if ev not in self.works or self.works[ev].done():self.works[ev]=asyncio.create_task(self.game(ev))
                self.store.setmeta('catalogue',dict(at=time.time(),games=list(self.games.values())))
            except Exception as e:self.store.log('catalogue_error','',str(e))
            await asyncio.sleep(300)
    async def poll_score(self,g):
        r=await asyncio.to_thread(self.reader.get,'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary',{'event':g['espn_id']})
        if not r['error'] and not identity(r['body'],g):r['error']='score_identity_or_season_mismatch'
        if not r['error']:
            try:parse_plays(r['body'])
            except Exception as e:r['error']='score_parse: '+str(e)
        rid=self.store.ingest(g['event'],r)
        if r['error']:raise RuntimeError(r['error'])
        self.store.setmeta('score_status_'+g['event'],dict(at=r['received'],status=r['body']['header']['competitions'][0].get('status'),receipt=rid))
    async def decide(self,g,boundary):
        event=g['event'];now=time.time()
        if self.db.execute('SELECT 1 FROM decisions WHERE event=? AND boundary=?',(event,boundary)).fetchone():return
        data={};reason=None
        if not self.ntp or now-self.clock_checked>45:reason='clock'
        elif (self.root/'HALT').exists():reason='manual_halt'
        elif now>=ts(self.cfg['entry_cutoff']):reason='weekend_cutoff'
        elif now-g['catalogued']>600:reason='catalogue_stale'
        versions,snap=self.store.source_at(event,boundary);state=state_at(versions,boundary,self.cfg)
        if not reason and not snap:reason='score_receipt_gap'
        if not reason and not leader(state):reason='score_or_clock_gate'
        if not reason:
            r=await self.get(event,'candles',BASE+'/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in g['markets']),start_ts=boundary-600,end_ts=boundary,period_interval=1))
            cm={m['market_ticker']:{int(c['end_period_ts']):c for c in m['candlesticks']} for m in r['body'].get('markets',[])}
            if not all(boundary in cm.get(m['ticker'],{}) for m in g['markets']):return
            pick,reason=choose(g,g['markets'],cm,state,boundary)
            data=dict(pick=pick,source_receipt=snap['id'],candle_receipt=r['receipt'])
            if time.time()>boundary+self.cfg['decision_deadline_seconds']:reason='decision_deadline'
        with self.db:self.db.execute('INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?)',(event,boundary,time.time(),reason or 'qualified',canonical(data)))
        if not reason:
            pick['decision_received']=time.time();pick['source_receipt']=snap['id'];pick['candle_receipt']=r['receipt']
            live=self.executor.armed()
            ident,why=self.store.reserve(pick,'live' if live else 'verify',time.time())
            if ident:asyncio.create_task(self.executor.submit(ident,g) if live else self.execute(ident,g))
            elif why:self.store.log('risk_skip',event,why)
    async def execute(self,ident,g):
        self.filling.add(ident);start=time.time()
        try:
            row=self.db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone();pick=json.loads(row['data'])
            if not self.ntp or (self.root/'HALT').exists():raise ValueError('halt_or_clock')
            latest,snap=self.store.source_at(g['event'],time.time());state=state_at(latest,time.time(),self.cfg)
            if not snap or leader(state)!=leader(pick['state']) or current_leader(latest,time.time())!=leader(pick['state']):raise ValueError('score_changed_or_stale')
            # Durable intent/payload generation is exercised, but there is no POST.
            payload=order_payload(pick,row['client_id']);samples=[]
            for phase in range(2):
                if phase:await asyncio.sleep(.05)
                b=self.stream.book(pick['ticker']);fill,why=depth(b,pick['side'],pick['cap'])
                if why:raise ValueError(why)
                samples.append(dict(fill=fill,book=b,received=time.time()))
            if time.time()-pick['boundary']>self.cfg['execution_deadline_seconds']:raise ValueError('execution_deadline_from_signal_minute')
            price=max(dec(pick['ask']),*[dec(x['fill']['vwap']) for x in samples])
            self.store.finish_simulated(ident,price,time.time(),dict(samples=samples,payload=payload,processing_ms=(time.time()-start)*1000,signal_minute_to_paper_fill_ms=(time.time()-pick['boundary'])*1000))
        except Exception as e:self.store.fail(ident,str(e))
        finally:self.filling.discard(ident)
    async def compare(self,g):
        # Bounded receive-only execution probe, not a qualifying signal or fill.
        for m in g['markets']:
            ticker=m['ticker'];before=self.stream.book(ticker)
            if not before:continue
            start=time.time();r=await self.get(g['event'],'rest_book',BASE+'/markets/'+ticker+'/orderbook',{'depth':10});after=self.stream.book(ticker)
            norm=lambda b:{s:sorted((str(dec(p).normalize()),str(dec(q).normalize())) for p,q in (b or {}).get(s,[]) if dec(q)>0) for s in ('yes_dollars','no_dollars')}
            stable=bool(after and before['_transport']['generation']==after['_transport']['generation'] and before['_transport']['seq']==after['_transport']['seq'])
            match=norm(r['body']['orderbook_fp'])==norm(after)
            probes=[]
            if after:
                for side in ('yes','no'):
                    opp='no' if side=='yes' else 'yes';lv=after[opp+'_dollars']
                    if not lv:continue
                    cap=1-max(dec(p) for p,q in lv if dec(q)>0);t0=time.perf_counter();fill,why=depth(after,side,cap,min_price=dec(0))
                    probes.append(dict(side=side,fill=fill,reason=why,lookup_and_depth_ms=(time.perf_counter()-t0)*1000))
            self.store.log('book_pair',g['event'],dict(ticker=ticker,stable=stable,match=match,elapsed=time.time()-start,probes=probes,game_live=g['start']<=time.time() and not g['done'],before=before,after=after,rest_receipt=r['receipt']))
    async def game(self,event):
        lastscore=lastprobe=lastsettle=0
        while self.running:
            g=self.games[event];now=time.time();active=g['start']-120<=now<=g['start']+8*3600 and not g['done']
            try:
                if self.canary or g['start']-1200<=now<=g['start']+8*3600 and not g['done']:
                    for m in g['markets']:self.stream.watch(m['ticker'],min(ts(m['close_time']),now+30))
                if active and now-lastscore>=self.cfg['score_poll_seconds']:
                    await self.poll_score(g);lastscore=time.time()
                boundary=int(time.time()//60)*60
                if active and boundary>=g['start'] and boundary+1<=time.time()<=boundary+self.cfg['decision_deadline_seconds']:
                    await self.decide(g,boundary)
            except Exception as e:self.store.log('game_error',event,str(e));await asyncio.sleep(2)
            await asyncio.sleep(.25 if active else 5)
    async def audit_loop(self):
        # One bounded background auditor avoids a synchronized 26-request burst.
        while self.running:
            for g in list(self.games.values()):
                if self.canary or g['start']-120<=time.time()<=g['start']+8*3600 and not g['done']:
                    try:await self.compare(g)
                    except Exception as e:self.store.log('probe_error',g['event'],str(e))
                await asyncio.sleep(.1)
            await asyncio.sleep(2)
    async def settlements(self):
        while self.running:
            for row in self.db.execute("SELECT DISTINCT ticker,event FROM orders WHERE status='filled'").fetchall():
                try:
                    r=await self.get(row['event'],'settlement',BASE+'/markets/'+row['ticker']);self.store.settle(r['body']['market'],r['received'])
                except Exception as e:self.store.log('settlement_error',row['event'],str(e))
            await asyncio.sleep(60)
    def report(self):
        from review import build_report
        report=build_report(self.store,self.cfg);live=self.executor.armed()
        report.update(heartbeat=time.time(),mode='LIVE' if live else 'VERIFY_ONLY',live_orders_enabled=live,ntp=self.ntp,ws=self.stream.status(),http=dict(self.reader.stats),dropped_frames=self.drops,frame_errors=self.frame_errors,frames_written=self.frame_count,next_kickoff=self.cfg['first_kickoff'])
        report['phase']='WAITING_FOR_KICKOFF' if time.time()<ts(self.cfg['first_kickoff']) else 'COLLECTING_LIVE_VERIFICATION' if not live else 'LIVE'
        if self.drops or self.frame_errors:report['operational_checks_pass']=False;report['blockers'].append('frame_journal_loss')
        if not report['ws']['connected']:report['operational_checks_pass']=False;report['blockers'].append('stream_disconnected')
        self.last_report=report
        tmp=self.root/'status.tmp';tmp.write_text(json.dumps(report,indent=2));tmp.replace(self.root/'status.json')
    async def maintenance(self):
        while self.running:
            self.report();await asyncio.sleep(5)
    async def run(self,duration=None):
        tasks=[asyncio.create_task(x()) for x in (self.clock,self.catalogue,self.maintenance,self.audit_loop,self.settlements,self.executor.run)]
        try:
            until=time.monotonic()+duration if duration else None
            while self.running and (until is None or time.monotonic()<until):
                if time.time()>ts(self.cfg['entry_cutoff']) and not self.db.execute("SELECT 1 FROM orders WHERE status IN ('pending','unknown','filled') LIMIT 1").fetchone():break
                await asyncio.sleep(.5)
        finally:
            self.running=False
            for t in tasks+list(self.works.values()):t.cancel()
            await asyncio.gather(*tasks,*self.works.values(),return_exceptions=True);self.stream.close();self.frame_stop.set();self.frame_writer.join(timeout=5);self.report()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=int);ap.add_argument('--canary',action='store_true');a=ap.parse_args()
    lock=(P/'observer.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    o=Observer(canary=a.canary)
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:setattr(o,'running',False))
    asyncio.run(o.run(a.seconds))
if __name__=='__main__':main()
