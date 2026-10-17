"""Independent GET-only forward probe for the frozen z>=2 candidate.

No credentials, order client, POST/DELETE methods, or live-trading switch.
Paper fills are displayed-depth estimates, never exchange fills.
Run --once for a smoke test or --minutes N for a bounded observation.
"""
from sizing import CONTRACTS
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import time
import urllib.request
import urllib.parse

ROOT=Path(__file__).resolve().parent/'paper_state'
BASE='https://api.elections.kalshi.com/trade-api/v2'
ASSETS=('BTC','ETH','SOL','XRP')

def get(url):
    if not url.startswith((BASE+'/', 'https://api.exchange.coinbase.com/')):
        raise ValueError('Unapproved data endpoint')
    req=urllib.request.Request(url,headers={'User-Agent':'crypto-paper-probe/1.0'},method='GET')
    with urllib.request.urlopen(req,timeout=15) as f:return json.load(f)

def timestamp(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def fee(p):return math.ceil((.07*p*(1-p)-1e-12)*10000)/10000
def append(name,row):
    ROOT.mkdir(exist_ok=True)
    with (ROOT/name).open('a') as f:f.write(json.dumps(row)+'\n')

def scan(asset,T,entered):
    series='KX'+asset+'15M'
    markets=get(BASE+'/markets?'+urllib.parse.urlencode(dict(series_ticker=series,status='open',limit=10)))['markets']
    market=next((m for m in markets if timestamp(m['open_time'])<=T<timestamp(m['close_time'])),None)
    rec=dict(observed_ts=time.time(),decision_ts=T,asset=asset,mode='PAPER_ONLY',rule='cushion_coinbase_z2')
    if not market:return dict(rec,status='no_current_market')
    tkr=market['ticker'];close=timestamp(market['close_time']);start=timestamp(market['open_time'])
    rem=(close-T)/60;rec.update(ticker=tkr,remaining_minutes=rem)
    if not 3<=rem<=10:return dict(rec,status='outside_window')
    if tkr in entered:return dict(rec,status='already_paper_entered')
    q=urllib.parse.urlencode(dict(start_ts=start,end_ts=T,period_interval=1))
    candles=get(BASE+'/series/'+series+'/markets/'+tkr+'/candlesticks?'+q).get('candlesticks',[])
    c=next((c for c in candles if c['end_period_ts']==T),None)
    if not c:return dict(rec,status='decision_candle_unavailable')
    try:b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
    except (KeyError,TypeError):return dict(rec,status='no_decision_quote')
    if not 0<=b<=a<=1:return dict(rec,status='invalid_quote')
    side='yes' if b+a>1 else 'no'
    bid,ask=(b,a) if side=='yes' else (1-a,1-b)
    rec.update(side=side,signal_bid=bid,signal_ask=ask,
               exchange_index=market.get('exchange_index',0),price_ranges=market.get('price_ranges'))
    if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02+1e-8:return dict(rec,status='price_gate')
    q=urllib.parse.urlencode(dict(granularity=60,start=T-95*60,end=T))
    raw=get(f'https://api.exchange.coinbase.com/products/{asset}-USD/candles?'+q)
    bars={int(r[0]):float(r[4]) for r in raw if int(r[0])<=T-60}
    keys=list(range(T-61*60,T,60))
    if start-60 not in bars or not all(k in bars for k in keys):return dict(rec,status='incomplete_spot')
    vals=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
    if sigma<=0:return dict(rec,status='zero_sigma')
    strike=bars[start-60];spot=bars[T-60]
    z=(1 if side=='yes' else -1)*(spot/strike-1)/(sigma*math.sqrt(rem))
    rec.update(z=z,spot=spot,strike_proxy=strike,sigma=sigma)
    if z<2:return dict(rec,status='cushion_gate')
    rec['close_ts']=close
    return refresh_book(rec)

def refresh_book(signal):
    rec=dict(signal)
    for k in ('execution_status','entry','count','note'):
        rec.pop(k,None)
    T=rec['decision_ts'];tkr=rec['ticker'];side=rec['side'];ask=rec['signal_ask']
    if not 0<=time.time()-T<=35:return dict(rec,status='signal_too_old')
    d=get(BASE+'/markets/'+tkr+'/orderbook?depth=10').get('orderbook_fp',{})
    levels=d.get('no_dollars' if side=='yes' else 'yes_dollars') or []
    remaining=float(CONTRACTS);cost=0.;worst=None
    for p,n in sorted([(1-float(p),float(n)) for p,n in levels]):
        if not 0<=p<=1 or n<=0:continue
        got=min(remaining,n);cost+=got*p;remaining-=got;worst=p
        if remaining<=1e-9:break
    rec['book_observed_ts']=time.time()
    rec['book_levels']=levels
    rec['book_worst_ask']=worst
    rec['book_available_count']=float(CONTRACTS)-remaining
    if rec['book_observed_ts']-T>35:return dict(rec,status='book_too_late')
    if remaining>1e-9:return dict(rec,status='insufficient_depth')
    if worst>min(.999,ask+.01)+1e-8 or cost/CONTRACTS<.85-1e-8:return dict(rec,status='book_outside_limit',observed_ask=cost/CONTRACTS)
    return dict(rec,status='paper_fill',entry=cost/CONTRACTS,count=CONTRACTS,
                note='Displayed-depth estimate; no order was placed.')

def reconcile():
    path=ROOT/'observations.jsonl';settled=ROOT/'settlements.jsonl'
    rows=[json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    finished={json.loads(l)['ticker'] for l in settled.read_text().splitlines()} if settled.exists() else set()
    entries={r['ticker']:r for r in rows if r['status']=='paper_fill'}
    for tkr,row in entries.items():
        if tkr in finished or time.time()<row['close_ts']+60:continue
        m=get(BASE+'/markets/'+tkr).get('market',{})
        if m.get('result') not in ('yes','no'):continue
        n=row.get('count',1)
        pnl=n*(float(m['result']==row['side'])-row['entry']-fee(row['entry'])-.01)
        append('settlements.jsonl',dict(ticker=tkr,result=m['result'],pnl_after_1c=pnl,mode='PAPER_ONLY',ts=time.time()))
    return set(entries)

def main():
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');p.add_argument('--minutes',type=float,default=1)
    args=p.parse_args();end=time.time()+max(0,args.minutes)*60;last=None
    while True:
        T=int(time.time()//60)*60
        if T!=last:
            entered=reconcile()
            for asset in ASSETS:
                try:r=scan(asset,T,entered)
                except Exception as e:r=dict(asset=asset,decision_ts=T,status='data_error',error=str(e)[:160],mode='PAPER_ONLY')
                append('observations.jsonl',r);print(json.dumps(r),flush=True)
            last=T
        if args.once or time.time()>=end:break
        time.sleep(min(5,max(.1,end-time.time())))

if __name__=='__main__':main()
