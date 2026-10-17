"""Conditional replay of live-recorded fills and risk-blocked opportunities.

Only public GET requests for final market outcomes; no authenticated client.
"""
import concurrent.futures
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from decimal import Decimal as D, ROUND_CEILING

import requests
from replay import P, ROOT, NAMES, compare, dump, etday, engine

START = 1790993763.965353
SNAPSHOT = ROOT/'kalshi_z3_frequency_2026_10_07/snapshot.sqlite3'


def public_markets(tickers):
    path = P/'recent_public_markets.json'
    cache = json.loads(path.read_text()) if path.exists() else {}
    def fetch(ticker):
        response = requests.get('https://api.elections.kalshi.com/trade-api/v2/markets/'+ticker,timeout=20)
        response.raise_for_status()
        market = response.json()['market']
        assert market['result'] in ('yes','no'), (ticker,market['result'])
        return ticker,{k:market[k] for k in ('ticker','result','settlement_ts','close_time')}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for ticker,market in pool.map(fetch,sorted(set(tickers)-cache.keys())):
            cache[ticker] = market
    dump(path,cache)
    return cache


def ts(iso):
    return datetime.fromisoformat(iso.replace('Z','+00:00')).timestamp()


def book_fill(event):
    lane = event['lane']
    _,cap = engine.limit_price(event)
    floor = D('.85') if lane==NAMES[0] else D('.93')
    # Stored opposing bids become favorite asks. Take the cheapest five only.
    asks = sorted((D(1)-D(p),D(q)) for p,q in event.get('book_levels',[]))
    left,cost,fee,worst = D(5),D(0),D(0),D(0)
    for price,quantity in asks:
        if quantity<=0:continue
        if price<floor:return None,'below_lane_floor'
        if price>cap: break
        take=min(left,quantity)
        cost+=take*price
        fee+=D('.07')*take*price*(1-price)
        worst=price
        left-=take
        if not left:break
    if left:return None,'insufficient_depth_inside_cap'
    return dict(entry=str(cost/5),fee=str(fee.quantize(D('.0001'),rounding=ROUND_CEILING)),
                cap=str(cap),book_worst=str(worst)),None


def main():
    c=sqlite3.connect(f'file:{SNAPSHOT}?mode=ro',uri=True)
    orders=[json.loads(r[0]) for r in c.execute('SELECT record FROM orders')]
    events=[dict(json.loads(e),event_ts=t) for t,e in c.execute(
        'SELECT ts,event FROM events WHERE ts>=? ORDER BY id',(START,))]
    actual=[r for r in orders if r['created_ts']>=START and D(r.get('filled','0'))>0]
    zero={r['ticker'] for r in orders if r['created_ts']>=START and D(r.get('filled','0'))==0}
    risk=[e for e in events if e.get('execution_status')=='Daily loss budget including open risk']
    markets=public_markets([r['ticker'] for r in actual]+[e['ticker'] for e in risk])
    candidates=[]
    for r in actual:
        market=markets[r['ticker']]
        assert market['result']==r['result']
        assert 0<D(r['filled'])<=5, (r['ticker'],r['filled'])
        exact_entry=D(r['cost'])/D(r['filled'])
        candidates.append(dict(ticker=r['ticker'],lane=r.get('lane',NAMES[0]),asset=r['asset'],
            t=r['signal']['decision_ts'],entered=r['created_ts'],close=r['close_ts'],
            settled=max(r['close_ts']+30,ts(market['settlement_ts'])),quantity=float(r['filled']),
            entry=str(exact_entry),fee=r['fees'],won=int(r['side']==r['result']),
            side=r['side'],origin='actual_fill',actual_pnl=r['pnl']))
    exclusions=Counter()
    excluded=[]
    for e in risk:
        if not 0<=e['event_ts']-e['decision_ts']<=35:
            exclusions['signal_age_over35s']+=1;continue
        if e['ticker'] in zero:
            exclusions['actual_zero_fill_market']+=1;continue
        fill,why=book_fill(e)
        if why:
            exclusions[why]+=1;excluded.append(dict(ticker=e['ticker'],lane=e['lane'],ts=e['event_ts'],reason=why));continue
        market=markets[e['ticker']]
        candidates.append(dict(ticker=e['ticker'],lane=e['lane'],asset=e['asset'],
            t=e['decision_ts'],entered=e['event_ts'],close=e['close_ts'],
            settled=max(e['close_ts']+30,ts(market['settlement_ts'])),quantity=5,
            won=int(e['side']==market['result']),side=e['side'],origin='risk_blocked_book',
            z=e['z'],rem=e['remaining_minutes'],**fill))
    seed=[r for r in orders if r['created_ts']<START and r.get('settled_day')==etday(START)]
    result,chosen=compare(candidates,stress='0',initial_cash='49.7534',seed=seed)
    expected={r['ticker'] for r in actual}
    observed={r['ticker'] for r in chosen['10']}
    actual_net=sum((D(r['pnl']) for r in actual),D(0))
    result['calibration']=dict(actual_fills=len(actual),actual_net=str(actual_net),
        baseline_missing=sorted(expected-observed),baseline_extra=sorted(observed-expected),
        pnl_difference=float(D(str(result['10']['net']))-actual_net))
    result['inputs']=dict(start=START,cutoff=max(e['event_ts'] for e in events),
        snapshot_sha256=hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
        risk_events=len(risk),risk_markets=len({e['ticker'] for e in risk}),
        executable_risk_events=sum(r['origin']=='risk_blocked_book' for r in candidates),
        exclusions=dict(exclusions),excluded=excluded,seed_day_pnl=str(sum((D(r.get('pnl') or 0) for r in seed),D(0))))
    for label,stress,delay in [('extra_1c','.01',0),('release_plus60s','0',60),('release_plus300s','0',300)]:
        rr,_=compare(candidates,stress=stress,initial_cash='49.7534',seed=seed,settlement_delay=delay)
        result[label]={k:v for k,v in rr.items() if k in ('10','15')}
    dump(P/'recent_candidates.json',candidates)
    dump(P/'recent_results.json',result)
    dump(P/'recent_ledgers.json',chosen)
    print(json.dumps({k:v for k,v in result.items() if k not in ('difference',)},indent=2),flush=True)
    print('ADDED',json.dumps(result['difference'],default=str),flush=True)
    assert expected==observed, result['calibration']
    assert abs(result['calibration']['pnl_difference'])<1e-8,result['calibration']


if __name__=='__main__':
    main()
