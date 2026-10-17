"""Read-only ledger snapshot and public historical GETs; isolated outputs."""
from pathlib import Path
import json,gzip,pickle,sqlite3,time,requests,datetime,hashlib,subprocess
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
OLD=R/'kalshi_all_lane_exits_2026_10_14';WIN=R/'kalshi_winner_z_recovery_2026_10_14'
LIVE=R/'kalshi_revalidation_2026_09_12/live_v1';CUTOFF=1792029600
def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def ts(v):return datetime.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
def main():
    c=sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro',uri=True);c.execute('pragma query_only=on')
    oo=[json.loads(x[0]) for x in c.execute('select record from orders')];c.close()
    oo=[o for o in oo if o.get('status')=='settled' and float(o.get('filled',0))>0 and o['close_ts']<=CUTOFF]
    assert len(oo)==461 and sum(o['side']==o['result'] for o in oo)==438
    save('orders.json',oo)
    d=pickle.load(gzip.open(OLD/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(WIN/'seconds.pkl.gz','rb'))
    known={r['ticker']:r for r in d['actual']};actual=[];session=requests.Session();(P/'fresh').mkdir(exist_ok=True)
    def get(name,endpoint,params=None):
        dest=P/'fresh'/name
        if not dest.exists():
            started=time.time();res=session.get('https://external-api.kalshi.com/trade-api/v2'+endpoint,params=params,timeout=15);res.raise_for_status()
            dest.write_text(json.dumps(dict(started=started,received=time.time(),url=res.url,response=res.json()))+'\n')
        return json.loads(dest.read_text())['response']
    for o in oo:
        t=o['ticker'];a=o['asset'];s=o['signal']
        if t in known:
            actual.append(known[t]);continue
        m=get(t+'_market.json','/markets/'+t)['market']
        assert m['result']==o['result']
        cc=get(t+'_candles.json','/series/KX'+a+'15M/markets/'+t+'/candlesticks',dict(start_ts=o['close_ts']-900,end_ts=o['close_ts'],period_interval=1))['candlesticks']
        candles={v['end_period_ts']:{**{'t':v['end_period_ts']},**{x+y:v.get(n,{}).get(k+'_dollars') for x,n in [('b','yes_bid'),('a','yes_ask')] for y,k in [('o','open'),('c','close'),('h','high'),('l','low')]}} for v in cc}
        d['markets'][t]=dict(asset=a,result=1 if o['result']=='yes' else -1,candles=candles,settled=ts(m['settlement_ts']))
        r=dict(ticker=t,asset=a,lane=o['lane'],t=s['decision_ts'],entered=s.get('execution_ack_ts',o['created_ts']),close=o['close_ts'],settled=ts(m['settlement_ts']),side=1 if o['side']=='yes' else -1,quantity=float(o['filled']),cost=float(o['cost']),entry_fee=float(o['fees']),entry_friction=0,hold_net=float(o['pnl']),won=int(o['side']==o['result']),entry_z=s['z'],entry_sigma=s['sigma'],reference=s['strike_proxy'],entry_spot=s['spot'],entry_gate=s['required_z'],ranges=m['price_ranges'])
        actual.append(r)
        raw=get(m['event_ticker']+'_chart.json','/live_data/events/'+m['event_ticker'],{'range':'1h'})['live_data']['details']
        assert raw['asset']==a
        start,values=ss[a];arr=raw['timeseries'];need=max(int(q['t'])//1000 for q in arr)+1-start
        if need>len(values):values=np.pad(values,(0,need-len(values)),constant_values=np.nan)
        for q in arr:
            at=int(q['t'])//1000
            if at>=start:values[at-start]=float(q['v'])
        ss[a]=(start,values)
    d['actual']=actual
    for r in actual:assert abs(r['quantity']*r['won']-r['cost']-r['entry_fee']-r['hold_net'])<1e-8
    for n,obj in [('inputs.pkl.gz',d),('seconds.pkl.gz',ss)]:
        with gzip.open(P/n,'wb',compresslevel=1) as f:pickle.dump(obj,f)
    save('actual_entries.json',actual)
    save('snapshot.json',dict(ts=time.time(),cutoff=CUTOFF,trades=len(actual),wins=sum(r['won'] for r in actual),losses=sum(not r['won'] for r in actual),hold_net=sum(r['hold_net'] for r in actual),inputs={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [OLD/'inputs.pkl.gz',WIN/'seconds.pkl.gz']},read_only_ledger=True,public_get_only=True))
    print((P/'snapshot.json').read_text())
if __name__=='__main__':main()
