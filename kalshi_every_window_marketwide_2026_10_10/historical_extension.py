"""Complete archived-history portion of the three frozen finalist tests."""
from core import *
from concurrent.futures import ThreadPoolExecutor
import re,daily,pregame
STARTDATE='2026-05-01';STARTTS=ts(STARTDATE+'T00:00:00Z')
def archive_markets(s):
    out=[];cursor='';seen=set()
    while True:
        r=get('/historical/markets',{'series_ticker':s,'limit':1000,**({'cursor':cursor} if cursor else {})})
        out+=r['markets'];cursor=r.get('cursor','')
        # Archive is newest first; continue to exhaustion to avoid relying on sort.
        if not cursor:break
        assert cursor not in seen;seen.add(cursor)
    return [m for m in out if ts(m['close_time'])>=STARTTS-86400]
def daily_collect():
    dest=P/'daily_extension';index=read(dest/'daily_index.json');existing={w['owner'] for w in index};jobs=[];allq=defaultdict(dict);archived_tickers=set()
    for s,kind,hour in [('KXAAAGASD','gas',23),('KXHIGHNY','temperature',12)]:
        ms=archive_markets(s);save(dest/('archived_'+s+'.json'),ms);archived_tickers.update(m['ticker'] for m in ms);groups=defaultdict(list);mult=float(read(dest/'daily'/s/'series.json')['fee_multiplier'])
        for m in ms:groups[m['event_ticker']].append(m)
        for owner,items in sorted(groups.items()):
            if owner in existing:continue
            match=re.search(r'-(\d{2}[A-Z]{3}\d{2})(?:-|$)',owner)
            if not match:continue
            dt=datetime.strptime(match.group(1),'%y%b%d').replace(tzinfo=ET);date=dt.date().isoformat()
            if not STARTDATE<=date<='2026-10-09' or any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):continue
            times=[int((dt-timedelta(days=1)).replace(hour=h).timestamp()) for h in (20,23)] if kind=='gas' else [int(dt.replace(hour=h).timestamp()) for h in (12,16)]
            index.append({'owner':owner,'event':owner,'asset':s,'family':kind,'date':date,'markets':items,'times':times,'mult':mult})
    # Rebuild missing quote jobs even if a prior interrupted run saved its index.
    # Only the frozen finalist checkpoint is extended, not the whole grid.
    for g in index:
        t=g['times'][1 if g['family']=='gas' else 0]
        path=dest/'daily_quotes'/(str(t)+'.json.gz')
        if path.exists():allq[t].update(read(path))
        for m in g['markets']:
            if m['ticker'] in archived_tickers and ts(m['open_time'])<=t<ts(m['close_time']) and m['ticker'] not in allq[t]:jobs.append((t,m))
    save(dest/'daily_index.json',index);print('ARCHIVE_DAILY_WINDOWS',len(index),'requests',len(jobs),flush=True)
    def work(job):
        t,m=job;r=get('/historical/markets/'+m['ticker']+'/candlesticks',{'start_ts':t-600,'end_ts':t+60,'period_interval':1});return t,m['ticker'],r.get('candlesticks',[])
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,(t,ticker,cs) in enumerate(pool.map(work,jobs),1):
            allq[t][ticker]=cs
            if i%100==0:print('ARCHIVE_DAILY_QUOTES',i,'/',len(jobs),flush=True)
    for t,cm in allq.items():save(dest/'daily_quotes'/(str(t)+'.json.gz'),cm)
    daily.P=dest;daily.main()
def wnba_collect():
    dest=P/'wnba_extension';s='KXWNBAGAME';index=read(dest/'pregame_index.json');existing={g['event'] for g in index};ms=archive_markets(s);save(dest/'archived_markets.json',ms)
    miles=read(dest/'milestones.json');mapping=defaultdict(list);groups=defaultdict(list);meta=read(dest/'series.json');jobs=[];gaps=[]
    for m in miles:
        et=m.get('details',{}).get('main_game_event_ticker')
        if et:mapping[et].append(m)
    for m in ms:groups[m['event_ticker']].append(m)
    for et,items in groups.items():
        if et in existing:continue
        mm=mapping[et]
        if len(mm)!=1:gaps.append({'event':et,'reason':'missing_schedule'});continue
        mi=mm[0];start=ts(mi['start_date']);date=day(start)
        if not STARTDATE<=date<='2026-10-09':continue
        if len(items)!=2 or any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):gaps.append({'event':et,'reason':'outcomes'});continue
        if start>=min(ts(m['close_time']) for m in items):gaps.append({'event':et,'reason':'schedule_after_close'});continue
        index.append({'asset':s,'owner':mi['id'],'event':et,'date':date,'start':start,'markets':items,'mult':float(meta['fee_multiplier']),'season_type':mi.get('details',{}).get('season_type'),'milestone':mi,'begin':STARTDATE})
        jobs.append((et,items,start))
    def work(job):
        et,items,start=job;path=dest/'pregame'/s/(et+'_quotes.json.gz')
        if not path.exists():
            cm={m['ticker']:get('/historical/markets/'+m['ticker']+'/candlesticks',{'start_ts':start-1860,'end_ts':start-180,'period_interval':1}).get('candlesticks',[]) for m in items};save(path,cm)
        return et
    print('ARCHIVE_WNBA_GAMES',len(index),'jobs',len(jobs),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,_ in enumerate(pool.map(work,jobs),1):
            if i%50==0:print('ARCHIVE_WNBA',i,'/',len(jobs),flush=True)
    save(dest/'pregame_index.json',index);save(dest/'archive_gaps.json',gaps);pregame.P=dest;pregame.main()
if __name__=='__main__':
    import sys
    daily_collect() if 'daily' in sys.argv else wnba_collect()
