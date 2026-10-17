"""Read-only source validation, bounded public books, and live preservation."""
import gzip, importlib.util, json, math, statistics, sys, time
from collections import Counter
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'extension'))
import pull as h

def save(name,obj):
    (P/name).write_text(json.dumps(obj,indent=2)+'\n')

def history():
    out={}
    for series in ('KXDJI','KXINXU','KXNASDAQ100U'):
        dest=P/'indices/data'/series
        markets=json.loads((dest/'markets.json').read_text())
        events={m['event_ticker']:m for m in markets}
        summaries=[]
        for f in dest.glob('*_chart.json.gz'):
            with gzip.open(f,'rt') as stream: raw=json.load(stream)
            d=raw['response']['live_data']['details'];m=events[d['event_ticker']]
            points={int(x['t'])//1000:float(x['v']) for x in d['timeseries']}
            close=h.ts(m['close_time']);last=max(points)
            exp=m.get('expiration_value')
            summaries.append(dict(event=d['event_ticker'],close=close,last=last,
                last_offset=last-close,interval=d.get('interval'),cadence=d.get('cadence'),
                first=min(points),points=len(points),last_value=points[last],expiration=exp,
                closest_offset=min(points,key=lambda x:abs(x-close))-close,
                last_difference=abs(points[last]-float(exp)) if exp else None))
        out[series]=summaries
        print('historical source',series,'last offsets',dict(Counter(x['last_offset'] for x in summaries)),flush=True)
    save('index_chart_audit.json',out)

def live_dji():
    raw=h.get('/markets',dict(series_ticker='KXDJI',status='open',limit=1000))
    save('dji_open_markets.json',raw)
    now=time.time();markets=raw['response']['markets']
    current=[m for m in markets if h.ts(m['open_time'])<=now<h.ts(m['close_time'])]
    if not current: save('dji_live_probe.json',dict(error='no current market',ts=now));return
    event=min(current,key=lambda m:h.ts(m['close_time']))['event_ticker']
    current=[m for m in current if m['event_ticker']==event and m.get('floor_strike') is not None]
    chart=h.get('/live_data/events/'+event,dict(range='1h'))
    save('dji_live_chart.json',chart)
    d=chart['response']['live_data']['details'];points={int(x['t'])//1000:float(x['v']) for x in d['timeseries']}
    T=h.ts(chart['retrieved_at_utc'])//60*60
    vals=[points.get(t) for t in range(T-3660,T,60)]
    summary=dict(ts=time.time(),event=event,latest_underlying_ts=max(points),underlying_age=time.time()-max(points),complete61=all(v is not None for v in vals),asset=d['asset'],interval=d.get('interval'),books=[])
    def favorite(m):
        b,a=float(m['yes_bid_dollars']),float(m['yes_ask_dollars'])
        return (1,b,a) if b+a>1 else (-1,1-a,1-b)
    # Listing quote fields may still be zero immediately after hourly rollover.
    # Choose underlying-adjacent strikes, then derive both sides from fresh books.
    spot=points[max(points)]
    selected=sorted(current,key=lambda m:(abs(float(m['floor_strike'])-spot),m['ticker']))[:6]
    for m in selected:
        response=h.get('/markets/'+m['ticker']+'/orderbook')
        save('dji_book_'+m['ticker']+'.json',response)
        book=response['response'].get('orderbook_fp',{})
        yes=max((float(p) for p,q in book.get('yes_dollars',[]) if float(q)>0),default=None)
        no=max((float(p) for p,q in book.get('no_dollars',[]) if float(q)>0),default=None)
        if yes is None or no is None:
            summary['books'].append(dict(ticker=m['ticker'],reason='incomplete_two_sided_book'));continue
        ya=1-no;side=1 if yes+ya>1 else -1
        bid,ask=(yes,ya) if side==1 else (no,1-yes)
        key='no_dollars' if side==1 else 'yes_dollars'
        levels=sorted((1-float(p),float(q)) for p,q in book.get(key,[]) if float(q)>0)
        counts={str(cap):sum(q for p,q in levels if p<=cap+1e-9) for cap in (.939,.975)}
        s=dict(ticker=m['ticker'],side=side,book_bid=bid,book_ask=ask,spread=ask-bid,volume=m['volume_fp'],book_received=h.ts(response['retrieved_at_utc']),minutes_to_expiry=(h.ts(m['close_time'])-time.time())/60,best_ask=levels[0][0] if levels else None,quantity_inside_caps=counts)
        summary['books'].append(s)
    save('dji_live_probe.json',summary);print('DJI live',summary,flush=True)

def preservation():
    location=P.parent/'kalshi_crypto_frequency_2026_10_09'
    spec=importlib.util.spec_from_file_location('preservation',location/'deploy_resilience.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    receipt=json.loads((location/'deployment_receipt.json').read_text())
    count=m.validate_hashes(receipt['source_hashes'])
    status=json.loads((m.STATE/'status.json').read_text())
    assert m.flags()==receipt['flags']
    assert m.configs(status)==receipt['config']
    services={n:m.service(n) for n in m.SERVICES}
    assert services==receipt['services_after']
    age=time.time()-status['ts']
    assert age<30 and status['mode']=='LIVE' and status['armed']
    assert not any(status.get(k) for k in ('stopped','error','halted'))
    result=dict(ts=time.time(),source_files=len(receipt['source_hashes']),checkpoint_files=count,config_unchanged=True,flags_unchanged=True,services=services,heartbeat_age=age,mode=status['mode'],copper_paused=not status['assets']['COPPER']['enabled'])
    save('preservation.json',result);print('preservation',result,flush=True)

if __name__=='__main__':
    history();live_dji();preservation()
