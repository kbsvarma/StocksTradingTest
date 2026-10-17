"""Bounded public GET observation. No credentials, orders or persistent service."""
import sys,time,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'kalshi_broad_review_2026_10_10'
sys.path.insert(0,str(BASE))
import common
common.P=ROOT;common.RAW=ROOT/'raw';common.RAW.mkdir(exist_ok=True)
from common import get,save,ts
from weather_model import forecast,CITIES
from replay_candidates import quote,sq,fee

spec=json.loads((BASE/'weather_paper_spec.json').read_text())
target=math.ceil(time.time()/900)*900
if target-time.time()>700:raise RuntimeError('Next horizon too distant for bounded probe')
print('Scheduled single horizon',target,flush=True)
while time.time()<target+3:time.sleep(min(5,target+3-time.time()))
out=[]
for city in CITIES:
    started=time.time()
    ms=get('/markets',{'series_ticker':city,'status':'open','limit':1000},False)['markets']
    live=[m for m in ms if ts(m['open_time'])<=target<ts(m['close_time'])]
    if not live:out.append({'city':city,'reason':'no_current'});continue
    close=min(ts(m['close_time']) for m in live)
    ms=[m for m in live if ts(m['close_time'])==close];h=(close-target)//60
    r={'city':city,'decision':target,'horizon':h,'started':started,'event':ms[0]['event_ticker'],'choices':[]}
    if h not in spec['horizons_minutes']:r['reason']='off_horizon';out.append(r);continue
    chart=get('/live_data/events/'+r['event'],{'range':'1h'},False);received=time.time()
    points=chart['live_data']['details']['timeseries']
    bars={int(p['t'])//1000:(float(p['v']['temp_f']),p['v']['status']) for p in points if p['v'].get('temp_f') is not None}
    pred=forecast(bars,target,close,'persistence',10)
    r.update(source_received=received,source_age=received-max(bars),forecast=pred,source_window_ok=pred is not None)
    save(ROOT/'probe_inputs'/f'{city}_source.json',chart)
    if pred is None:r['reason']='missing_source_window';out.append(r);continue
    raw=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in ms),'start_ts':target-660,'end_ts':target,'period_interval':1},False)
    save(ROOT/'probe_inputs'/f'{city}_candles.json',raw)
    cm={m['market_ticker']:{c['end_period_ts']:c for c in m['candlesticks']} for m in raw['markets']}
    errs=spec['calibrations'][city][str(h)]['errors_f'];choices=[];counts={'markets':len(ms),'quote':0,'volume':0,'price_spread':0,'edge':0}
    for m in ms:
        cs=cm.get(m['ticker'],{});q=quote(cs.get(target))
        if q is None:continue
        counts['quote']+=1
        if sum(float(c.get('volume_fp') or 0) for t,c in cs.items() if target-600<t<=target)<5:continue
        counts['volume']+=1
        py=(sum(pred+e>float(m['floor_strike']) for e in errs)+1)/(len(errs)+2)
        for side,p in [('yes',py),('no',1-py)]:
            bid,ask=sq(q,side)
            if not .60<=ask<.95 or ask-bid>.02000001:continue
            counts['price_spread']+=1;edge=p-ask-fee(ask,1)/5-.01
            if edge<.02:continue
            counts['edge']+=1
            choices.append({'ticker':m['ticker'],'side':side,'ask':ask,'bid':bid,'p':p,'edge':edge,'cap':min(.95,ask+.01)})
    r['counts']=counts;r['choices']=choices
    if choices:
        best=max(choices,key=lambda x:(x['edge'],-x['ask'],x['ticker']))
        b=get('/markets/'+best['ticker']+'/orderbook',{'depth':100},False);save(ROOT/'probe_inputs'/f'{city}_book.json',b)
        asks=sorted((1-float(p),float(q)) for p,q in b['orderbook_fp']['no_dollars' if best['side']=='yes' else 'yes_dollars'])
        best.update(book_received=time.time(),quantity_at_cap=sum(q for p,q in asks if p<=best['cap']+1e-8),best_ask=asks[0][0] if asks else None)
        r['selected']=best
    else:r['reason']='no_signal'
    r['completed']=time.time();out.append(r)
save(ROOT/'live_horizon_check.json',out)
print(json.dumps(out,indent=2),flush=True)
