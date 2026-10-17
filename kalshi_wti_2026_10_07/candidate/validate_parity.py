"""Offline comparison of deployable scanner against frozen research features."""
import datetime as dt
import hashlib
import json
import math
import pickle
from pathlib import Path
from collections import Counter
from unittest.mock import patch
import market_data as m
from pyth_data import PythMinutes
from lanes import NEW_ASSETS,Z2,Z3,lane_for,route_for
from engine import limit_price

P=Path(__file__).resolve().parent.parent
RESEARCH=P.parent/'kalshi_noncrypto_tuning_2026_10_07'
SOURCE=P.parent/'kalshi_noncrypto15m_2026_10_07'
iso=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()

def main():
    markets=pickle.loads((RESEARCH/'features.pickle').read_bytes())
    bars=pickle.loads((SOURCE/'underlying_minutes.pickle').read_bytes())
    choices=json.loads((RESEARCH/'configurations.json').read_text())
    chosen={'COPPER':1239,'NATGAS':0,'EURUSD':1183,'WTI':1238}
    count=Counter();diff=0;caps=0
    for market in markets:
        asset=market['asset']
        if asset not in NEW_ASSETS:continue
        cfg=choices[chosen[asset]]
        for f in market['features']:
            T=f['t'];side='yes' if f['side']==1 else 'no'
            bc,ac=(f['bid'],f['ask']) if side=='yes' else (1-f['ask'],1-f['bid'])
            candle=dict(end_period_ts=T,yes_bid=dict(close_dollars=str(bc)),yes_ask=dict(close_dollars=str(ac)))
            meta=dict(ticker=market['ticker'],event_ticker=market['ticker'].rsplit('-',1)[0],open_time=iso(market['start']),close_time=iso(market['close']),notional_value_dollars='1',exchange_index=route_for(dict(asset=asset)),price_ranges=market['ranges'])
            chart=dict(live_data=dict(details=dict(asset=asset,timeseries=[dict(t=(t+59)*1000,v=bars[asset][t]) for t in range(T-3660,T,60)])))
            def get(url):
                if '/markets?' in url:return dict(markets=[meta])
                if '/candlesticks?' in url:return dict(candlesticks=[candle])
                if '/live_data/' in url:return chart
                raise AssertionError('Unexpected endpoint '+url)
            with patch.object(m,'PYTH',PythMinutes()),patch.object(m,'get',side_effect=get),patch('time.time',return_value=T+3),patch.object(m,'refresh_book',side_effect=lambda s:dict(s,status='qualified')):
                for lane in (Z2,Z3):
                    r=m.scan(asset,T,set(),lane)
                    low=lane==Z2;floor=cfg['floor2'] if low else cfg['floor3'];ceil=.95 if low else .98
                    zbase=cfg['z2'] if low else cfg['z3']
                    required=max(.5,zbase+cfg['slope']*math.log2(f['rem']/5))
                    if not cfg['lo']<=f['rem']<=cfg['hi']:expected='outside_window'
                    elif not floor-1e-8<=f['ask']<ceil-1e-8 or f['ask']-f['bid']>.02000001:expected='price_gate'
                    elif f['z']<required:expected='cushion_gate'
                    else:expected='qualified'
                    if r['status']!=expected:raise AssertionError((market['ticker'],T,lane,r['status'],expected))
                    if 'z' in r:
                        diff=max(diff,abs(r['z']-f['z']))
                        if abs(r['z']-f['z'])>1e-10:raise AssertionError('z mismatch')
                    count[asset+'_'+expected]+=1;count['scans']+=1
                    if expected=='qualified':
                        _,cap=limit_price(r)
                        if cap>lane_for(r).cap:raise AssertionError('Price cap relaxed')
                        caps+=1
    report=dict(passed=True,scanner_decisions=count['scans'],counts=dict(count),max_z_difference=diff,cap_checks=caps,
                frozen_rules=chosen,orders=False,mode='OFFLINE_SCANNER_PARITY',
                caveat='Research history reused; latest routing applied to historical contracts. No fill-depth or publication-latency claim.',
                source_hashes={n:hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest() for n in ('market_data.py','lanes.py','pyth_data.py','engine.py')})
    (P/'scanner_parity.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
