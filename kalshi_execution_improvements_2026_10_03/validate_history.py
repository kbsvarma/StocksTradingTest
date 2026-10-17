"""Recompute historical scanner decisions from raw saved Coinbase closes."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import sys
from unittest.mock import patch
import market_data
from engine import limit_price
from lanes import Z2,Z3

P=Path(__file__).resolve().parent
RESEARCH=P.parent/'kalshi_parallel_design_2026_10_02'
sys.path.insert(0,str(RESEARCH))
from strategy import RULES,qualifies,tick_cap

def main():
    markets=pickle.loads((RESEARCH/'features.pickle').read_bytes())
    folders=[P.parent/'kalshi_restart_audit_2026_09_12/fresh_data',RESEARCH/'fresh_data',P.parent/'kalshi_execution_audit_2026_10_02/fresh_data']
    bars={a:{} for a in market_data.ASSETS}
    for folder in folders:
        for a in bars:
            for line in (folder/f'spot_{a}.jsonl').open():
                for r in json.loads(line)['rows']:bars[a][int(r[0])]=float(r[4])
    iso=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat().replace('+00:00','Z')
    results={}
    for lane,name in [(Z2,'current_signal95'),(Z3,'premium_z3')]:
        rule=next(r for r in RULES if r.name==name);checks=0;passes=0;max_delta=0.
        for m in markets:
            if m['close']>=1790899200:continue # exclusive October 2 UTC
            for i,f in enumerate(m['features']):
                expected=qualifies(rule,f)
                # Every qualifying signal, plus deterministic rejected controls.
                if not expected and (f['t']//60)%97:continue
                if not 3<=f['rem']<=10:continue
                T=f['t'];b,a=(f['bid'],f['ask']) if f['side']==1 else (1-f['ask'],1-f['bid'])
                market=dict(ticker=m['ticker'],open_time=iso(m['start']),close_time=iso(m['close']),price_ranges=m['ranges'],exchange_index=2,notional_value_dollars='1.0000')
                cap=tick_cap(rule,f,m['ranges']);entry=max(rule.lo,min(rule.hardcap,cap or rule.hardcap))
                raw=[[k,0,0,0,bars[m['asset']][k],0] for k in range(T-61*60,T,60) if k in bars[m['asset']]]
                raw.append([T,0,0,0,999999999.,0]) # forming minute must be ignored
                def get(url):
                    if '/orderbook' in url:return dict(orderbook_fp={'no_dollars' if f['side']==1 else 'yes_dollars':[[str(round(1-entry,8)),'5']]})
                    if 'coinbase' in url:return raw
                    if '/candlesticks?' in url:return dict(candlesticks=[dict(end_period_ts=T,yes_bid={'close_dollars':str(b)},yes_ask={'close_dollars':str(a)})])
                    return dict(markets=[market])
                with patch.object(market_data,'get',side_effect=get),patch.object(market_data.time,'time',return_value=T+3):
                    r=market_data.scan(m['asset'],T,set(),lane=lane)
                assert (r['status']=='paper_fill')==expected,(lane,m['ticker'],T,r['status'],expected)
                if expected:
                    assert abs(r['z']-f['z'])<1e-9,(lane,r['z'],f['z'])
                    max_delta=max(max_delta,abs(r['z']-f['z']))
                    assert abs(float(limit_price(r)[1])-cap)<1e-8,(lane,m['ticker'],limit_price(r),cap)
                    passes+=1
                checks+=1
        results[lane]=dict(decisions_checked=checks,qualifying_signals=passes,max_z_difference=max_delta)
    out=dict(validation='PASS',raw_completed_bars=True,forming_bars_ignored=True,real_orders_enabled=False,results=results)
    (P/'historical_validation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))

if __name__=='__main__':main()
