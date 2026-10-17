"""Five bounded decision cycles using public GETs; no accounts or order transport."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from decimal import Decimal as D
import gzip
import json
import math
from pathlib import Path
import requests
import threading
import time
import replay as r
r.ASSETS = ("USDJPY","AUDUSD","USDCAD")

P=r.P/'live_probe'
P.mkdir(exist_ok=True)
BASE='https://api.elections.kalshi.com/trade-api/v2'
lock=threading.Lock()
next_request=0.
local=threading.local()
bars={a:{} for a in r.ASSETS}

def get(path,params=None):
    global next_request
    if not hasattr(local,'session'):local.session=requests.Session()
    with lock:
        pause=max(0,next_request-time.monotonic())
        next_request=max(next_request,time.monotonic())+.4
    if pause:time.sleep(pause)
    sent=time.time()
    res=local.session.get(BASE+path,params=params,timeout=5)
    res.raise_for_status()
    return dict(sent_ts=sent,received_ts=time.time(),url=res.url,response=res.json())

def absorb(asset,chart):
    d=chart['response']['live_data']['details']
    assert d['asset']==asset
    for pt in d['timeseries']:
        t,p=int(pt['t']),float(pt['v'])
        if t%60000==59000 and p>0 and math.isfinite(p):bars[asset][t//60000*60]=p

def warm(asset):
    try:
        markets=get('/markets',dict(series_ticker='KX'+asset+'15M',status='settled',limit=1))
        market=markets['response']['markets'][0]
        chart=get('/live_data/events/'+market['event_ticker'],{'range':'1h'})
        absorb(asset,chart)
        with gzip.open(P/(asset+'_warm.json.gz'),'wt') as f:json.dump(dict(markets=markets,chart=chart),f)
        return dict(asset=asset,minutes=len(bars[asset]))
    except Exception as e:return dict(asset=asset,error=str(e))

def depth(book,side,rule=None,cap=D(1)):
    key='no_dollars' if side==1 else 'yes_dollars'
    levels=sorted((D(1)-D(p),D(q)) for p,q in book.get(key,[]))
    n,cost=D(0),D(0)
    for price,quantity in levels:
        if quantity<=0:continue
        if rule is not None and price<rule.floor:return dict(reason='book_below_floor')
        if price>cap:break
        take=min(D(5)-n,quantity);n+=take;cost+=take*price
        if n==5:return dict(count=5,entry=float(cost/5),worst=float(price))
    return dict(count=float(n),reason='insufficient_depth')

def observe(asset,T):
    row=dict(asset=asset,decision_ts=T,lanes={})
    raw={}
    try:
        raw['markets']=get('/markets',dict(series_ticker='KX'+asset+'15M',status='open',limit=20))
        market=next((m for m in raw['markets']['response']['markets'] if r.ts(m['open_time'])<=T<r.ts(m['close_time'])),None)
        if market is None:return dict(row,error='no_current_market')
        row.update(ticker=market['ticker'],close_ts=r.ts(market['close_time']),exchange_index=market['exchange_index'])
        raw['chart']=get('/live_data/events/'+market['event_ticker'],{'range':'1h'})
        absorb(asset,raw['chart'])
        raw['candles']=get('/series/KX'+asset+'15M/markets/'+market['ticker']+'/candlesticks',
             dict(start_ts=r.ts(market['open_time']),end_ts=T,period_interval=1))
        raw['book']=get('/markets/'+market['ticker']+'/orderbook')
        book=raw['book']['response'].get('orderbook_fp',{})
        row['book_ts']=raw['book']['received_ts']
        row['book_age_from_decision']=row['book_ts']-T
        row['five_contract_depth']={str(side):depth(book,side) for side in (1,-1)}
        c=next((c for c in raw['candles']['response'].get('candlesticks',[]) if c['end_period_ts']==T),None)
        if c is None:return dict(row,error='missing_decision_candle')
        bid,ask=float(c['yes_bid']['close_dollars']),float(c['yes_ask']['close_dollars'])
        if not 0<=bid<=ask<=1:return dict(row,error='invalid_quote')
        side=1 if bid+ask>1 else -1
        bid,ask=(bid,ask) if side==1 else (1-ask,1-bid)
        rem=(row['close_ts']-T)/60
        if rem<=0:return dict(row,error='closed')
        f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask)
        extra=r.feature(bars[asset],r.ts(market['open_time']),T,rem,side)
        if extra:f.update(extra)
        row['features']=f
        for lane,rule in r.LANES.items():
            if not 3<=rem<=10:status='outside_window'
            elif not rule.floor-D('.00000001')<=D(str(ask))<rule.ceiling or ask-bid>.02000001:status='price_gate'
            elif extra is None:status='missing_underlying_minutes'
            elif f['z']<rule.z:status='z_gate'
            else:
                _,cap=r.limit_price(dict(lane=lane,signal_ask=ask,side='yes' if side==1 else 'no',price_ranges=market['price_ranges']))
                fill=depth(book,side,rule,cap)
                status='five_contract_opportunity' if fill.get('count')==5 and row['book_age_from_decision']<=35 else fill.get('reason','signal_expired')
                row['lanes'][lane]=dict(status=status,cap=str(cap),**fill)
                continue
            row['lanes'][lane]=dict(status=status)
        return row
    except Exception as e:return dict(row,error=type(e).__name__+': '+str(e))
    finally:
        raw['complete_minute_bars']={str(k):v for k,v in bars[asset].items() if T-7200<=k<T}
        with gzip.open(P/f'{T}_{asset}_raw.json.gz','wt') as f:json.dump(raw,f)

def main():
    started=time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        warmups=list(pool.map(warm,r.ASSETS))
        (P/'warmups.json').write_text(json.dumps(warmups,indent=2))
        print('warmups',warmups,flush=True)
        for cycle in range(2):
            T=(int(time.time())//60+1)*60
            time.sleep(max(0,T+2-time.time()))
            rows=list(pool.map(lambda a:observe(a,T),r.ASSETS))
            with (P/'observations.jsonl').open('a') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
            counts=Counter(v['status'] for row in rows for v in row['lanes'].values())
            print('cycle',cycle+1,'ts',T,'statuses',dict(counts),'errors',[x for x in rows if x.get('error')],flush=True)
    (P/'receipt.json').write_text(json.dumps(dict(started=started,finished=time.time(),cycles=2,assets=list(r.ASSETS),public_reads_only=True),indent=2))

if __name__=='__main__':main()
