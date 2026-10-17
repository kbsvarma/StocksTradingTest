"""A bounded public snapshot of books and source completeness, no orders."""
import datetime as dt,json,math,statistics,time
from decimal import Decimal as D
import requests
import pull as p
P=p.P
def depth(book,side):
    opp='no' if side=='yes' else 'yes'
    asks=sorted((1-D(px),D(n)) for px,n in book.get(opp+'_dollars',[]) if D(n)>0)
    bids=sorted(((D(px),D(n)) for px,n in book.get(side+'_dollars',[]) if D(n)>0),reverse=True)
    if not asks or not bids:return dict(available=False)
    return dict(bid=float(bids[0][0]),ask=float(asks[0][0]),spread=float(asks[0][0]-bids[0][0]),
        best_ask_contracts=float(asks[0][1]),five_at_best=asks[0][1]>=5,
        depth_le_939=float(sum(n for px,n in asks if px<=D('.939'))),depth_le_975=float(sum(n for px,n in asks if px<=D('.975'))))
rows=[];raw={}
for asset in ('DOGE','HYPE','NEAR','ZEC','BNB'):
    listed=p.get('/markets',dict(series_ticker='KX'+asset+'15M',status='open',limit=20))
    now=time.time();m=next(x for x in listed['response']['markets'] if p.ts(x['open_time'])<=now<p.ts(x['close_time']))
    response=p.get('/markets/'+m['ticker']+'/orderbook');book=response['response']['orderbook_fp']
    T=int(time.time())//60*60
    url='https://api.exchange.coinbase.com/products/'+asset+'-USD/candles'
    sent=time.time();request=requests.get(url,params=dict(granularity=60,start=T-3660,end=T),timeout=10);request.raise_for_status()
    data=request.json();prices={int(x[0]):float(x[4]) for x in data};expected=list(range(T-3660,T,60))
    missing=[t for t in expected if t not in prices]
    rows.append(dict(asset=asset,ticker=m['ticker'],book_received=response['retrieved_at_utc'],volume_so_far=float(m['volume_fp']),
        minutes_remaining=(p.ts(m['close_time'])-time.time())/60,yes=depth(book,'yes'),no=depth(book,'no'),
        source_minute=T,complete_source_bars=61-len(missing),missing_source_minutes=missing,
        preopen_reference_present=p.ts(m['open_time'])-60 in prices,
        warning='This is a book/source snapshot, not a qualified minute signal or fill.'))
    raw[asset]=dict(markets=listed,book=response,coinbase=dict(url=request.url,received=time.time(),seconds=time.time()-sent,rows=data))
p.write(P/'liquidity_snapshot.json',rows);p.write(P/'liquidity_raw.json',raw)
print(json.dumps(rows,indent=2))
