"""Capture the one WTI winner settling during analysis. Public GETs only."""
from pathlib import Path
import sqlite3, json, requests, time, gzip, pickle, datetime
import numpy as np
P=Path(__file__).resolve().parent; OLD=P.parent/'kalshi_all_lane_exits_2026_10_14'
cutoff=1792029600  # Oct14 22:00 ET; fixed research cutoff, not a trading filter.
c=sqlite3.connect(f'file:{P.parent}/kalshi_revalidation_2026_09_12/live_v1/state/orders.sqlite3?mode=ro',uri=True)
c.execute('pragma query_only=on')
orders=[json.loads(x[0]) for x in c.execute('select record from orders')]
orders=[o for o in orders if o.get('status')=='settled' and float(o.get('filled',0))>0 and o['side']==o['result'] and o['close_ts']<=cutoff]
d=pickle.load(gzip.open(OLD/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(OLD/'seconds.pkl.gz','rb'))
prior={r['ticker'] for r in d['actual']}; additions=[o for o in orders if o['ticker'] not in prior]
assert [o['ticker'] for o in additions]==['KXWTI15M-26OCT142200-00']
session=requests.Session()
for o in additions:
    t=o['ticker']; event=t.rsplit('-',1)[0]
    for name,endpoint,params in [(t+'_market.json','/markets/'+t,None), (event+'_chart.json','/live_data/events/'+event,{'range':'1h'})]:
        f=P/name
        if not f.exists():
            started=time.time(); response=session.get('https://external-api.kalshi.com/trade-api/v2'+endpoint,params=params,timeout=15); response.raise_for_status()
            f.write_text(json.dumps(dict(started=started,received=time.time(),url=response.url,response=response.json()))+'\n')
    market=json.loads((P/(t+'_market.json')).read_text())['response']['market']
    s=o['signal'];a=o['asset'];r=dict(ticker=t,asset=a,lane=o['lane'],t=s['decision_ts'],entered=s.get('execution_ack_ts',o['created_ts']),close=o['close_ts'],side=1 if o['side']=='yes' else -1,won=1,entry_z=s['z'],entry_sigma=s['sigma'],reference=s['strike_proxy'],entry_spot=s['spot'],hold_net=float(o['pnl']))
    assert market['result']==o['result']; d['actual'].append(r);d['markets'][t]=dict(result=r['side'])
    arr=json.loads((P/(event+'_chart.json')).read_text())['response']['live_data']['details']['timeseries']
    start,values=ss[a];need=max(int(q['t'])//1000 for q in arr)+1-start
    if need>len(values):values=np.pad(values,(0,need-len(values)),constant_values=np.nan)
    for q in arr:
        at=int(q['t'])//1000
        if at>=start:values[at-start]=float(q['v'])
    ss[a]=(start,values)
    # Complete-minute rolling controls only where all 61 historical values exist.
    bb={int(q['t'])//60000*60:float(q['v']) for q in arr if int(q['t'])%60000==59000}
    for at in range(int(r['entered'])//60*60,int(r['close']),60):
        vv=[bb.get(k) for k in range(at-3660,at,60)]
        if len(vv)==61 and all(x is not None for x in vv):d['stats'][a][at]=(vv[-1],float(np.std(np.diff(np.log(vv)))))
for name,value in [('inputs.pkl.gz',d),('seconds.pkl.gz',ss)]:
    with gzip.open(P/name,'wb',compresslevel=1) as f:pickle.dump(value,f)
(P/'added_winners.json').write_text(json.dumps(additions,indent=2)+'\n')
(P/'cohort_cutoff.json').write_text(json.dumps(dict(close_cutoff=cutoff,close_cutoff_et='2026-10-14 22:00 ET',winner_count=len(orders),checked_ts=time.time()),indent=2)+'\n')
print('Captured',len(orders),'winners through 22:00 ET; added',len(additions))
