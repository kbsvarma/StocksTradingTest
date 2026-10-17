"""Public GET-only independent reconstruction; no live bot imports or changes."""
import concurrent.futures,datetime as dt,json,math,statistics,time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
flat=json.loads((P/'classified.json').read_text())
start=dt.datetime(2026,10,15,tzinfo=ET).timestamp()
rows=[x for x in flat if start<=x['T'] and x['category'] not in ('outside_window','no_current_market','already_owned')]
def fetch_coinbase(asset):
    selected=[x for x in rows if x['asset']==asset]
    first=int(min(x['T'] for x in selected)-3660);last=int(max(x['T'] for x in selected))
    bars={};receipts=[]
    with requests.Session() as session:
        for begin in range(first,last,290*60):
            end=min(last,begin+290*60)
            url=f'https://api.exchange.coinbase.com/products/{asset}-USD/candles'
            began=time.time();r=session.get(url,params=dict(granularity=60,start=begin,end=end),timeout=8)
            r.raise_for_status();raw=r.json();receipts.append(dict(url=r.url,received=time.time(),elapsed=time.time()-began,data=raw))
            bars.update({int(x[0]):float(x[4]) for x in raw})
    return asset,bars,receipts
allbars={};allreceipts={}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for asset,bars,receipts in pool.map(fetch_coinbase,('BTC','ETH','SOL','XRP')):
        allbars[asset]=bars;allreceipts[asset]=receipts
(P/'coinbase_receipts.json').write_text(json.dumps(allreceipts))
checked=[];incomplete=[]
for x in rows:
    r=x['record'];T=int(x['T']);bars=allbars[x['asset']];remaining=r['remaining_minutes']
    reference=int(T+remaining*60-960);keys=list(range(T-3660,T,60))
    if reference not in bars or any(k not in bars for k in keys):incomplete.append([x['asset'],T]);continue
    values=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(b/a) for a,b in zip(values,values[1:]))
    z=(1 if r['side']=='yes' else -1)*(values[-1]/bars[reference]-1)/(sigma*math.sqrt(remaining))
    required=r.get('required_z',3 if x['lane']=='premium_z3' or x['asset']=='BTC' else 2)
    checked.append(dict(asset=x['asset'],lane=x['lane'],ticker=r['ticker'],T=T,category=x['category'],ask=r.get('signal_ask'),
        z=z,required=required,captured_z=r.get('z'),delta=None if r.get('z') is None else z-r['z'],
        qualified_now=z>=required,remaining=remaining,spot=values[-1],reference=bars[reference],sigma=sigma))
samples=json.loads((P/'audit.json').read_text())['closest']
def quote_check(rec):
    T=rec['decision_ts'];ticker=rec['ticker'];base='https://api.elections.kalshi.com/trade-api/v2'
    with requests.Session() as session:
        r=session.get(base+'/markets/'+ticker,timeout=8);r.raise_for_status();market=r.json()['market']
        url=base+'/series/'+ticker.split('-')[0]+'/markets/'+ticker+'/candlesticks'
        r=session.get(url,params=dict(start_ts=T-60,end_ts=T,period_interval=1),timeout=8);r.raise_for_status();payload=r.json()
    c=next(c for c in payload['candlesticks'] if c['end_period_ts']==T)
    b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars']);side='yes' if a+b>1 else 'no'
    bid,ask=(b,a) if side=='yes' else (1-a,1-b)
    opened=dt.datetime.fromisoformat(market['open_time'].replace('Z','+00:00')).timestamp()
    expected_open=T+rec['remaining_minutes']*60-900
    return dict(ticker=ticker,lane=rec['lane'],T=T,side_matches=side==rec['side'],quote_matches=abs(ask-rec['signal_ask'])<1e-9 and abs(bid-rec['signal_bid'])<1e-9,
        open_time_matches=opened==expected_open,observed_ask=rec['signal_ask'],refetched_ask=ask,raw_market=market,raw_candles=payload)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:quotes=list(pool.map(quote_check,samples))
(P/'quote_receipts.json').write_text(json.dumps(quotes))
captured=[r for r in checked if r['delta'] is not None]
price_blocked=[r for r in checked if r['qualified_now'] and r['category'].startswith('price_')]
summary=dict(ts=time.time(),mode='READ_ONLY_PUBLIC_HISTORICAL_INPUT_RECHECK_NOT_A_FILL_BACKTEST',
    reconstructed_lane_minutes=len(checked),incomplete=incomplete,compared_recorded_z=len(captured),
    max_abs_z_difference=max(abs(r['delta']) for r in captured),
    changed_z_decisions=[r for r in captured if (r['captured_z']>=r['required'])!=r['qualified_now']],
    eight_closest_quote_checks=[{k:v for k,v in q.items() if k not in ('raw_market','raw_candles')} for q in quotes],
    strong_but_price_blocked=price_blocked,
    strong_but_price_blocked_counts={a:{l:sum(r['asset']==a and r['lane']==l for r in price_blocked) for l in ('cushion_z2','premium_z3')} for a in allbars})
(P/'independent_recheck.json').write_text(json.dumps(summary,indent=2))
(P/'reconstructed_minutes.json').write_text(json.dumps(checked))
print(json.dumps({k:v for k,v in summary.items() if k!='strong_but_price_blocked'},indent=2))
