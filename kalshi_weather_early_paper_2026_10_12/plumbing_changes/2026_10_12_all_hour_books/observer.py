"""Isolated public GET-only weather paper observer. No financial API client."""
import asyncio,fcntl,hashlib,json,logging,re,signal,time
from pathlib import Path
import requests
from engine import CITIES,SCENARIOS,ts,canonical,choose,book
from store import Store,status

P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
ALLOWED=re.compile(r'^/(?:markets|markets/orderbooks|markets/candlesticks|markets/KXTEMP[A-Z0-9.-]+|series/KXTEMP[A-Z]+|live_data/weather/(?:miami|chicago|la-coastal|nyc)(?:/calibrations)?)$')
LOG=logging.getLogger('weather-early-paper')

class Client:
    def __init__(self):
        self.session=requests.Session();self.session.trust_env=False
        self.session.headers.update({'User-Agent':'WeatherEarly-PaperResearch/1.0','Cache-Control':'no-cache'})
        self.lock=asyncio.Lock();self.next=0
    async def get(self,path,params=None):
        if not ALLOWED.fullmatch(path):raise ValueError('Forbidden endpoint: '+path)
        async with self.lock:
            await asyncio.sleep(max(0,self.next-time.monotonic()))
            started=time.time();mono=time.monotonic();url=requests.Request('GET',BASE+path,params=params).prepare().url
            code=0;body=None;headers={};error=None
            try:
                r=await asyncio.to_thread(self.session.get,url,timeout=(3,5),allow_redirects=False)
                code=r.status_code;headers={k:r.headers.get(k) for k in ('Date','Age','ETag','Cache-Control')}
                r.raise_for_status()
                if code!=200:raise ValueError('Unexpected status '+str(code))
                body=r.json()
            except (requests.RequestException,ValueError) as ex:error=str(ex)[:1000]
            self.next=max(mono+1,time.monotonic()+(30 if code==429 else 0))
            return {'url':url,'started':started,'received':time.time(),'duration':time.monotonic()-mono,'status':code,'body':body,'headers':headers,'error':error}

class Observer:
    def __init__(self,root=P):
        self.root=Path(root);self.cfg=json.loads((self.root/'frozen_config.json').read_text())
        assert self.cfg['paper_only'] is True
        for name,expected in self.cfg['frozen_files'].items():
            assert hashlib.sha256((self.root/name).read_bytes()).hexdigest()==expected,(name,'hash mismatch')
        self.training=json.loads((self.root/'training.json').read_text())
        assert self.training['end']<time.time()
        self.store=Store(self.root/'paper.sqlite',self.cfg);self.db=self.store.db;self.client=Client()
        self.running=True;self.clock_ok=False;self.markets={};self.configs={};self.fees={};self.volumes={};self.exec_busy=False
        self.startup=set(CITIES);self.last_clock=(time.time(),time.monotonic());self.active_books=None
        self.store.setmeta('boot',time.time())
    async def get(self,path,params=None):
        r=await self.client.get(path,params);r['id'],r['hash']=self.store.receipt(r)
        age=r['headers'].get('Age')
        if r['error'] or r['duration']>self.cfg['max_http'] or (age is not None and float(age)>1):
            self.store.log('http_failure',{'url':r['url'],'error':r['error'],'duration':r['duration'],'age':age})
            raise RuntimeError(r['error'] or 'slow_or_cached_response')
        return r
    async def background_yield(self):
        while self.exec_busy and self.running:await asyncio.sleep(.1)
    async def batch(self,tickers):
        tickers=sorted(set(tickers));assert 0<len(tickers)<=100
        r=await self.get('/markets/orderbooks',{'tickers':tickers})
        r['books']={b['ticker']:b for b in r['body'].get('orderbooks',[])}
        return r
    async def clock(self):
        while self.running:
            wall,mono=time.time(),time.monotonic();jump=abs((wall-self.last_clock[0])-(mono-self.last_clock[1]));self.last_clock=(wall,mono)
            try:
                proc=await asyncio.create_subprocess_exec('timedatectl','show','-p','NTPSynchronized','--value',stdout=asyncio.subprocess.PIPE)
                out,_=await asyncio.wait_for(proc.communicate(),5);self.clock_ok=out.strip()==b'yes' and jump<2
            except Exception as ex:self.clock_ok=False;self.store.log('clock_error',ex)
            self.store.setmeta('clock',{'checked':time.time(),'ntp_synchronized':self.clock_ok,'jump_seconds':jump})
            await asyncio.sleep(30)
    async def catalogue(self):
        while self.running:
            for series,city in CITIES.items():
                try:
                    await self.background_yield()
                    if series not in self.configs or time.time()-self.configs[series]['received']>3600:
                        c=await self.get('/live_data/weather/'+city+'/calibrations');self.configs[series]=c
                        f=await self.get('/series/'+series);meta=f['body']['series']
                        self.fees[series]=meta.get('fee_type') in ('quadratic','quadratic_with_maker_fees') and float(meta.get('fee_multiplier',0))==1
                        self.store.setmeta('fee_'+series,{'valid':self.fees[series],'receipt':f['id'],'data':meta})
                    await self.background_yield()
                    r=await self.get('/markets',{'series_ticker':series,'status':'open','limit':1000})
                    if r['body'].get('cursor'):raise RuntimeError('Catalogue pagination required')
                    self.markets[series]={'items':r['body']['markets'],'receipt':r['id'],'received':r['received']}
                except Exception as ex:self.store.log('catalogue_error',str(ex));LOG.warning('Catalogue %s: %s',city,ex)
            self.store.setmeta('catalogue',{'received':time.time(),'events':{s:sorted(set(m['event_ticker'] for m in r['items'])) for s,r in self.markets.items()}})
            await asyncio.sleep(60)
    async def candles(self,series,items):
        boundary=int(time.time()//60)*60;old=self.volumes.get(series)
        if old and old['boundary']==boundary:return
        await self.background_yield()
        r=await self.get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in items),'start_ts':boundary-600,'end_ts':boundary,'period_interval':1})
        volumes={m['market_ticker']:sum(float(c.get('volume_fp') or 0) for c in m['candlesticks'] if boundary-600<c['end_period_ts']<=boundary) for m in r['body'].get('markets',[])}
        self.volumes[series]={'boundary':boundary,'received':r['received'],'receipt':r['id'],'values':volumes}
    async def sources(self):
        while self.running:
            started=time.monotonic();now=time.time();active={}
            for series,record in self.markets.items():
                items=[m for m in record['items'] if 40<=ts(m['close_time'])-now<=360]
                if items and now-record['received']<120:active[series]=items
            pre=None;sources={}
            try:
                for series,items in active.items():await self.candles(series,items)
                if active:
                    await self.background_yield();pre=await self.batch([m['ticker'] for v in active.values() for m in v])
            except Exception as ex:self.store.log('prebook_or_volume_error',ex)
            for series,city in CITIES.items():
                try:
                    if series not in self.configs:continue
                    await self.background_yield()
                    r=await self.get('/live_data/weather/'+city,{'last_sec':900,'detailed':'true'})
                    if r['body'].get('city')!=city or r['body'].get('units')!='fahrenheit':raise RuntimeError('Unexpected source city/units')
                    known={c['config_version'] for c in self.configs[series]['body']['calibrations']}
                    if r['body'].get('config_version') not in known:
                        self.configs[series]=await self.get('/live_data/weather/'+city+'/calibrations')
                        self.store.log('configuration_refreshed',city)
                        continue  # first use only on the next prospective source receipt
                    source=self.store.ingest(series,r,self.configs[series]['body']['calibrations'],series in self.startup)
                    self.startup.discard(series)
                    if source:sources[series]=source
                except Exception as ex:self.store.log('source_error',str(ex))
            # The post-source book is the decision book. Earlier quotes are
            # retained strictly for timing comparison, never backdated fills.
            if active and sources:
                try:
                    await self.background_yield();post=await self.batch([m['ticker'] for v in active.values() for m in v])
                    now=time.time()
                    for series,source in sources.items():
                        if series not in active or not self.fees.get(series) or not self.clock_ok:continue
                        if now-source['received']>10 or now-self.configs[series]['received']>3900:continue
                        if now>self.store.meta('started')+self.cfg['stop_days']*86400 or (self.root/'HALT_PAPER').exists():continue
                        vol=self.volumes.get(series)
                        if not vol or now-vol['boundary']>75:continue
                        grouped={}
                        for m in active[series]:grouped.setdefault(m['event_ticker'],[]).append(m)
                        for event,items in grouped.items():
                            if not self.cfg['min_remaining']<=ts(items[0]['close_time'])-now<=self.cfg['max_remaining']:continue
                            pick,reasons=choose(items,post['books'],source,self.training,now,vol['values'],self.cfg)
                            evidence={'source':source,'prebook_receipt':pre['id'] if pre else None,'postbook_receipt':post['id'],
                                      'config_receipt':self.configs[series]['id'],'catalogue_receipt':self.markets[series]['receipt'],
                                      'volume_receipt':vol['receipt'],'volume_boundary':vol['boundary'],'rejections':reasons}
                            did=self.store.decision(pick,event,series,source['source_t'],now,'eligible' if pick else 'no_edge_or_price',evidence)
                            if did and pick:
                                for scenario in SCENARIOS:self.store.attempt(did,scenario,pick,now)
                                LOG.info('Qualified %s %s ask %.2f edge %.4f',pick['ticker'],pick['side'],pick['ask'],pick['edge'])
                except Exception as ex:self.store.log('decision_error',ex);LOG.exception('Decision failure')
            await asyncio.sleep(max(1,self.cfg['source_poll_seconds']-(time.monotonic()-started)))
    async def execute(self,attempts):
        if not attempts:return
        self.exec_busy=True
        try:
            picks={a['id']:json.loads(a['data']) for a in attempts};tickers=sorted(set(p['ticker'] for p in picks.values()))
            mr=await self.get('/markets',{'tickers':','.join(tickers),'limit':1000})
            markets={m['ticker']:m for m in mr['body']['markets']}
            valid=[]
            for a in attempts:
                p=picks[a['id']];m=markets.get(p['ticker'])
                if not self.clock_ok:self.store.fail(a['id'],'clock');continue
                if not m or m.get('status')!='active' or m.get('settlement_ts') or ts(m['close_time'])<=time.time():self.store.fail(a['id'],'closed_or_inactive');continue
                if time.time()>a['due']+self.cfg['execution_deadline']:self.store.fail(a['id'],'execution_deadline');continue
                valid.append(a)
            if not valid:return
            first=await self.batch([picks[a['id']]['ticker'] for a in valid]);accepted=[];first_values={}
            for a in valid:
                p=picks[a['id']];fill,why=book(first['books'].get(p['ticker'],{}),p['side'],p['cap'],p['qty'])
                if first['received']>a['due']+self.cfg['execution_deadline'] or first['received']>=p['close']:why='execution_deadline_or_closed'
                with self.db:self.db.execute('INSERT INTO book_checks(attempt,phase,receipt,received,data,reason) VALUES(?,?,?,?,?,?)',(a['id'],0,first['id'],first['received'],canonical({'book':fill,'market_receipt':mr['id']}),why))
                if why:self.store.fail(a['id'],why)
                else:accepted.append(a);first_values[a['id']]=fill
            if not accepted:return
            await asyncio.sleep(self.cfg['second_book_seconds'])
            second=await self.batch([picks[a['id']]['ticker'] for a in accepted])
            for a in accepted:
                p=picks[a['id']];fill,why=book(second['books'].get(p['ticker'],{}),p['side'],p['cap'],p['qty'])
                if second['received']>a['due']+self.cfg['execution_deadline'] or second['received']>=p['close']:why='execution_deadline_or_closed'
                if not self.clock_ok:why='clock'
                with self.db:self.db.execute('INSERT INTO book_checks(attempt,phase,receipt,received,data,reason) VALUES(?,?,?,?,?,?)',(a['id'],1,second['id'],second['received'],canonical({'book':fill}),why))
                if why:self.store.fail(a['id'],why)
                else:
                    price=max(p['ask'],first_values[a['id']]['vwap'],fill['vwap'])
                    self.store.fill(a['id'],price,second['received']);LOG.info('PAPER fill %s %s qty5 %.4f',a['scenario'],p['ticker'],price)
        except Exception as ex:
            for a in attempts:self.store.fail(a['id'],'execution_error:'+str(ex)[:250])
            self.store.log('execution_error',ex);LOG.exception('Execution failure')
        finally:self.exec_busy=False
    async def executor(self):
        while self.running:
            now=time.time();due=[]
            for a in self.store.rows("SELECT * FROM attempts WHERE status='pending' ORDER BY created,id"):
                p=json.loads(a['data'])
                if now>=p['close']:self.store.fail(a['id'],'closed_before_execution');continue
                if a['due'] is None:
                    pub=self.db.execute('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(a['series'],p['source']['source_t'])).fetchone()
                    if pub:
                        a['due']=max(a['created'],pub['received'])
                        with self.db:self.db.execute('UPDATE attempts SET due=? WHERE id=?',(a['due'],a['id']))
                    else:continue
                if now>a['due']+self.cfg['execution_deadline']:self.store.fail(a['id'],'execution_deadline');continue
                if now>=a['due']:due.append(a)
            if due:await self.execute(due)
            await asyncio.sleep(.2)
    async def settlements(self):
        while self.running:
            try:
                positions=self.store.rows('SELECT DISTINCT ticker FROM positions WHERE settled IS NULL')
                if positions:
                    await self.background_yield();r=await self.get('/markets',{'tickers':','.join(p['ticker'] for p in positions),'limit':1000})
                    for m in r['body']['markets']:self.store.settle(m,r)
            except Exception as ex:self.store.log('settlement_error',ex)
            await asyncio.sleep(30)
    async def reporting(self):
        while self.running:
            self.store.setmeta('heartbeat',time.time());data=status(self.store);target=self.root/'status.json';temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(target)
            if time.time()>data['review']['entries_end'] and all(not p['open'] and not p['pending'] for p in data['portfolios'].values()):
                self.store.setmeta('completed',time.time());return
            await asyncio.sleep(5)
    async def run(self):
        lock=open(self.root/'observer.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        tasks=[asyncio.create_task(f()) for f in (self.clock,self.catalogue,self.sources,self.executor,self.settlements,self.reporting)]
        try:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        finally:
            self.running=False
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.db.close();lock.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(Observer().run())
