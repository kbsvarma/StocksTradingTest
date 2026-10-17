"""Bounded public GET collector, self-contained receipts and resumable cache."""
from core import *
import concurrent.futures as F
import requests
import threading
import time
SERIES=('KXMLBKS','KXNFLREC','KXNFLPASSYDS','KXNBAPTS')
START=ts('2026-09-01T04:00:00Z');END=ts('2026-10-12T04:00:00Z')
lock=threading.Lock();clock=0
def get(path,params):
    global clock
    url=requests.Request('GET','https://external-api.kalshi.com/trade-api/v2'+path,params=params).prepare().url
    key=hashlib.sha256(url.encode()).hexdigest();p=P/'data/props_raw'/f'{key}.json.gz'
    if p.exists():return read(p)['response']
    for attempt in range(5):
        with lock:
            wait=max(0,clock-time.monotonic());clock=max(clock,time.monotonic())+.8
        time.sleep(wait)
        try:
            rr=requests.get(url,timeout=25);rr.raise_for_status();d=rr.json()
            save(str(p.relative_to(P)),dict(url=url,received=time.time(),response=d));return d
        except requests.RequestException:
            if attempt==4:raise
            time.sleep(2**attempt)
def pages(path,params,key):
    rows=[];cursor='';seen=set()
    while True:
        d=get(path,{**params,**({'cursor':cursor} if cursor else {})});rows+=d[key];cursor=d.get('cursor')
        if not cursor:return rows
        assert cursor not in seen;seen.add(cursor)
def main():
    raw=ROOT/'kalshi_noncrypto_consistency_2026_10_10/milestones_all.json';miles=read(raw)
    byevent=C.defaultdict(list)
    for mi in miles:
        keys=set(mi.get('related_event_tickers',[])+mi.get('primary_event_tickers',[]))
        main=mi.get('details',{}).get('main_game_event_ticker')
        if main:keys.add(main)
        for e in keys:byevent[e].append(mi)
    bysuffix=C.defaultdict(list)
    for mi in miles:
        e=mi.get('details',{}).get('main_game_event_ticker')
        if e and '-' in e:bysuffix[e.split('-',1)[1]].append(mi)
    index=[];gaps=[];jobs=[]
    for s in SERIES:
        info=get('/series/'+s,{})['series'];save('data/props/'+s+'_series.json',info)
        ms=pages('/markets',dict(series_ticker=s,status='settled',min_close_ts=START-86400,max_close_ts=END+86400,limit=1000),'markets')
        save('data/props/'+s+'_markets.json',ms);groups=C.defaultdict(list)
        for m in ms:groups[m['event_ticker']].append(m)
        for event,items in groups.items():
            found=byevent.get(event,[]) or bysuffix.get(event.split('-',1)[1],[])
            found={m['id']:m for m in found if m.get('start_date')}
            if len(found)!=1:gaps.append(dict(event=event,reason='missing_or_ambiguous_schedule'));continue
            mi=next(iter(found.values()));start=ts(mi['start_date'])
            if not START<=start<END:continue
            valid=[]
            for m in items:
                players=[v for k,v in (m.get('custom_strike') or {}).items() if k.endswith('_player')]
                if len(players)!=1 or not m.get('settlement_ts') or m.get('settlement_value_dollars') is None:continue
                if not m.get('floor_strike') or m.get('strike_type') not in ('greater','greater_or_equal'):continue
                m=dict(m,player=players[0]);valid.append(m)
            if not valid:continue
            t=(start-1800)//60*60;path=P/'data/props_quotes'/f'{event}.json.gz'
            index.append(dict(event=event,asset=s,game=mi['id'],start=start,t=t,mult=float(info['fee_multiplier']),markets=valid))
            if not path.exists():
                for i in range(0,len(valid),100):jobs.append((event,valid[i:i+100],t-600,t+120))
        print('CATALOG',s,len(ms),'events',len(groups),'mapped',sum(x['asset']==s for x in index),flush=True)
    save('props_index.json',index);save('props_mapping_gaps.json',gaps)
    partial=C.defaultdict(dict)
    def batch(job):
        event,ms,lo,hi=job;data=get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in ms),start_ts=lo,end_ts=hi,period_interval=1))
        return event,{r['market_ticker']:r['candlesticks'] for r in data.get('markets',[])}
    with F.ThreadPoolExecutor(max_workers=3) as ex:
        for i,result in enumerate(ex.map(batch,jobs),1):
            event,data=result;partial[event].update(data)
            if i%40==0:print('QUOTES',i,'/',len(jobs),flush=True)
    for event,cm in partial.items():save('data/props_quotes/'+event+'.json.gz',cm)
    save('props_collection_receipt.json',dict(games=len(index),jobs=len(jobs),mapping_gaps=len(gaps),finished=dt.datetime.now(dt.timezone.utc).isoformat()))
    print('PROPS DONE',len(index),len(jobs),flush=True)
if __name__=='__main__':main()
