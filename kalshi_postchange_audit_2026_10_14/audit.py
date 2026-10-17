"""Read-only ledger and paced public candle audit; never imports an order client."""
import collections,datetime,hashlib,json,sqlite3,statistics,time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

P=Path(__file__).resolve().parent
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
ET=ZoneInfo('America/New_York')
def stamp(hour,minute):
    return datetime.datetime(2026,10,14,hour,minute,tzinfo=ET).timestamp()
db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
events=[]
for ts,raw in db.execute('select ts,event from events where ts>=? order by id',(stamp(13,0),)):
    r=json.loads(raw);r['_saved_ts']=ts;events.append(r)
orders=[json.loads(r) for r, in db.execute('select record from orders')]
attempts=[json.loads(r) for r, in db.execute('select record from order_attempts')]
db.close()
(P/'events.json').write_text(json.dumps(events))
(P/'orders.json').write_text(json.dumps(orders))
(P/'attempts.json').write_text(json.dumps(attempts))
(P/'status.json').write_text((L/'state/status.json').read_text())
unique={}
for r in events:
    if r.get('ticker') and r.get('signal_ask') is not None:
        unique.setdefault(r['ticker'],[]).append(r)
session=requests.Session();session.headers['User-Agent']='read-only-candle-publication-audit/1.0'
comparisons=[];errors=[]
out=P/'public_receipts.jsonl'
assert not out.exists(),'Preserve prior receipts; do not rerun over them'
for index,(ticker,rows) in enumerate(sorted(unique.items())):
    # Avoid the trader's initial burst and cap diagnostic traffic at one GET/2s.
    time.sleep(2)
    if time.time()%60<9:time.sleep(9-time.time()%60)
    start=min(r['decision_ts'] for r in rows)-900;end=max(r['decision_ts'] for r in rows)
    url='https://api.elections.kalshi.com/trade-api/v2/series/'+ticker.split('-')[0]+'/markets/'+ticker+'/candlesticks'
    response=None
    try:
        began=time.time();response=session.get(url,params=dict(start_ts=start,end_ts=end,period_interval=1),timeout=3)
        received=time.time();data=response.json()
        with out.open('a') as f:f.write(json.dumps(dict(ticker=ticker,url=response.url,status=response.status_code,started=began,received=received,data=data))+'\n')
        response.raise_for_status()
        candles={c['end_period_ts']:c for c in data.get('candlesticks',[])}
        for r in rows:
            c=candles.get(r['decision_ts'])
            if c is None:errors.append(dict(ticker=ticker,decision=r['decision_ts'],error='historical candle missing'));continue
            b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
            side='yes' if b+a>1 else 'no';bid,ask=(b,a) if side=='yes' else (1-a,1-b)
            comparisons.append(dict(ticker=ticker,asset=r['asset'],lane=r['lane'],decision_ts=r['decision_ts'],live_status=r['status'],live_side=r['side'],live_ask=r['signal_ask'],live_bid=r['signal_bid'],final_side=side,final_ask=ask,final_bid=bid,changed=side!=r['side'] or abs(ask-r['signal_ask'])>1e-8 or abs(bid-r['signal_bid'])>1e-8,scan_age=r.get('scan_completed_ts',r['_saved_ts'])-r['decision_ts']))
    except Exception as exc:
        errors.append(dict(ticker=ticker,error=str(exc)))
        # Do not add load through automatic diagnostic retries.
        if response is not None and response.status_code==429:break
    if (index+1)%10==0:print(json.dumps(dict(progress=index+1,total=len(unique),comparisons=len(comparisons),changed=sum(r['changed'] for r in comparisons),errors=len(errors))),flush=True)
(P/'candle_comparison.json').write_text(json.dumps(dict(ts=time.time(),comparisons=comparisons,errors=errors),indent=2))
print(json.dumps(dict(done=True,tickers=len(unique),comparisons=len(comparisons),changed=sum(r['changed'] for r in comparisons),errors=errors)),flush=True)
