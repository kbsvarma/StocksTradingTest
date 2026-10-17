"""Bounded public-GET audit of completed candle revisions. Never imports a trader."""
import concurrent.futures, datetime, json, math, statistics, time
from pathlib import Path
import requests

P = Path(__file__).resolve().parent
BASE = 'https://api.elections.kalshi.com/trade-api/v2'
ASSETS = ('BTC', 'ETH', 'SOL', 'XRP')
def get(url, params):
    start = time.time()
    r = requests.get(url, params=params, timeout=4)
    r.raise_for_status()
    return dict(start=start, end=time.time(), url=r.url, data=r.json())
def sample(asset, T, offset):
    row = dict(asset=asset, T=T, offset=offset)
    try:
        # A quarter-hour contract closes at the next exact boundary.
        close = (T//900+1)*900
        from zoneinfo import ZoneInfo
        stamp=datetime.datetime.fromtimestamp(close, ZoneInfo('America/New_York'))
        ticker='KX'+asset+'15M-'+stamp.strftime('%y%b%d%H%M').upper()+'-'+stamp.strftime('%M')
        row['ticker']=ticker
        row['coinbase']=get('https://api.exchange.coinbase.com/products/'+asset+'-USD/candles',dict(granularity=60,start=T-95*60,end=T))
        row['kalshi']=get(BASE+'/series/KX'+asset+'15M/markets/'+ticker+'/candlesticks',dict(start_ts=close-900,end_ts=T,period_interval=1))
        bars={int(r[0]):float(r[4]) for r in row['coinbase']['data']}
        c=next(c for c in row['kalshi']['data']['candlesticks'] if c['end_period_ts']==T)
        b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
        side='yes' if b+a>1 else 'no';bid,ask=(b,a) if side=='yes' else (1-a,1-b)
        vals=[bars[k] for k in range(T-3660,T,60)];reference=bars[close-960]
        sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
        rem=(close-T)/60
        z=(1 if side=='yes' else -1)*(vals[-1]/reference-1)/(sigma*math.sqrt(rem))
        row.update(z=z,spot=vals[-1],reference=reference,sigma=sigma,ask=ask,bid=bid,side=side,remaining=rem,
            original=3<=rem<=10 and .85-1e-8<=ask<.95-1e-8 and ask-bid<=.02000001 and z>=(3 if asset=='BTC' else 2),
            premium=3<=rem<=10 and .93-1e-8<=ask<.98-1e-8 and ask-bid<=.02000001 and z>=3)
    except Exception as exc: row['error']=str(exc)
    return row

start=(int(time.time())//60+1)*60
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for T in range(start,start+12*60,60):
        for offset in (.25,2,5,10):
            while time.time()<T+offset:time.sleep(min(.2,T+offset-time.time()))
            rows=list(pool.map(lambda a:sample(a,T,offset),ASSETS))
            with (P/'arrival_receipts.jsonl').open('a') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
        print(json.dumps(dict(T=T,completed=True)),flush=True)
