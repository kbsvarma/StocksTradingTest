"""Finite USD/CAD GET-only timing study. Never imports an order client or engine."""
import argparse
import datetime as dt
import gzip
import json
from pathlib import Path
import sys
import time
import requests
from urllib.parse import urlencode

P=Path(__file__).resolve().parent
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(LIVE))
from pyth_data import PythMinutes
from lanes import lane_for,Z2,Z3
from price_rules import limit_price
import math,statistics

BASE='https://api.elections.kalshi.com/trade-api/v2'
S=requests.Session();S.headers['User-Agent']='usdcad-operational-audit/1.0'
counter=0
def get(path):
    global counter
    assert path.startswith(BASE+'/')
    started=time.time();response=S.get(path,timeout=3);ended=time.time();counter+=1
    receipt=dict(id=counter,started=started,ended=ended,url=path,status=response.status_code,
        headers={k:response.headers[k] for k in ('Date','Age','Cache-Control') if k in response.headers})
    if response.status_code==200:
        data=response.json();receipt['body']=data
    else:data=None
    with gzip.open(P/'public_receipts.jsonl.gz','at') as f:f.write(json.dumps(receipt)+'\n')
    response.raise_for_status();return data,receipt
def read(path):return get(path)[0]
def stamp(v):return int(dt.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp())
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--minutes',type=int,default=6);args=parser.parse_args()
    started=time.time();end=started+min(args.minutes,10)*60;pyth=PythMinutes();samples=[]
    pyth.warm('USDCAD',read,BASE)
    done=set();market=None;last_minute=None;offsets=(.25,1,2,3,5,8,15,24)
    while time.time()<end:
        now=time.time();T=int(now//60)*60
        pending=next((o for o in offsets if (T,o) not in done and now>=T+o),None)
        if pending is None:time.sleep(.1);continue
        done.add((T,pending))
        # Do not batch obsolete early offsets when collection begins mid-minute.
        if now-(T+pending)>1.5:continue
        row=dict(decision_ts=T,scheduled_offset=pending,started=now)
        try:
            if T!=last_minute:
                raw,receipt=get(BASE+'/markets?'+urlencode(dict(series_ticker='KXUSDCAD15M',status='open',limit=10)))
                market=next((m for m in raw['markets'] if stamp(m['open_time'])<=T<stamp(m['close_time'])),None)
                last_minute=T
            if market is None:raise ValueError('No current CAD market')
            row['ticker']=market['ticker'];close=stamp(market['close_time']);start=stamp(market['open_time'])
            row['minutes']=(close-T)/60
            candles,cr=get(BASE+'/series/KXUSDCAD15M/markets/'+market['ticker']+'/candlesticks?'+urlencode(dict(start_ts=start,end_ts=T,period_interval=1)))
            candle=next((c for c in candles.get('candlesticks',[]) if c['end_period_ts']==T),None)
            row['candle_received']=cr['ended'];row['candle_present']=bool(candle)
            raw,sr=get(BASE+'/live_data/events/'+market['event_ticker']+'?range=1h')
            row['chart_received']=sr['ended'];pyth.absorb('USDCAD',raw,sr['ended'])
            keys=list(range(T-3660,T,60));bars=pyth.bars.get('USDCAD',{})
            row['missing_bars']=[k for k in keys if k not in bars];row['reference_present']=start-60 in bars
            row['source_complete']=not row['missing_bars'] and row['reference_present']
            book,br=get(BASE+'/markets/'+market['ticker']+'/orderbook?depth=10');row['book_received']=br['ended']
            if candle:
                bid=float(candle['yes_bid']['close_dollars']);ask=float(candle['yes_ask']['close_dollars'])
                side='yes' if bid+ask>1 else 'no';bid,ask=(bid,ask) if side=='yes' else (1-ask,1-bid)
                row.update(side=side,signal_bid=bid,signal_ask=ask)
                if row['source_complete']:
                    values=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(values,values[1:]))
                    z=(1 if side=='yes' else -1)*(values[-1]/bars[start-60]-1)/(sigma*math.sqrt(row['minutes'])) if sigma>0 else None
                    row.update(sigma=sigma,z=z,spot=values[-1],reference=bars[start-60])
                row['lanes']={}
                for name in (Z2,Z3):
                    rule=lane_for(dict(asset='USDCAD',lane=name));m=row['minutes']
                    qualified=bool(rule.min_minutes<=m<=rule.max_minutes and float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8 and 0<=ask-bid<=.02000001 and row.get('z') is not None and row['z']>=rule.threshold(m))
                    cap=None;depth=0;below_floor=False
                    if float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8:
                        signal=dict(asset='USDCAD',lane=name,side=side,signal_ask=ask,price_ranges=market['price_ranges'])
                        cap=float(limit_price(signal)[1])
                        levels=book.get('orderbook_fp',{}).get('no_dollars' if side=='yes' else 'yes_dollars',[]) or []
                        eligible=[(1-float(px),float(q)) for px,q in levels if float(q)>0]
                        below_floor=any(px<float(rule.floor)-1e-8 for px,q in eligible)
                        depth=sum(q for px,q in eligible if float(rule.floor)-1e-8<=px<=cap+1e-8)
                    row['lanes'][name]=dict(required_z=rule.threshold(m),qualified=qualified,cap=cap,depth_inside_cap=depth,below_floor=below_floor,book_executable=qualified and depth>=5 and not below_floor)
            row['completed']=time.time()
        except Exception as exc:row.update(error=str(exc)[:250],completed=time.time())
        samples.append(row)
        with (P/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        (P/'collector_status.json').write_text(json.dumps(dict(ts=time.time(),started=started,end=end,samples=len(samples),requests=counter,finished=False)))
    (P/'collector_status.json').write_text(json.dumps(dict(ts=time.time(),started=started,end=end,samples=len(samples),requests=counter,finished=True)))
    print(json.dumps(dict(finished=True,samples=len(samples),requests=counter,errors=sum('error' in r for r in samples))))
if __name__=='__main__':main()
