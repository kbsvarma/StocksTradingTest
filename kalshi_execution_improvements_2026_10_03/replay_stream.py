"""Reconstruct captured books against the SAME frozen live signals. No orders."""
from pathlib import Path
from decimal import Decimal as D
import collections, gzip, hashlib, json, sys

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P/'candidate'))
from engine import limit_price
from lanes import lane_for

def book_state(levels, signal):
    rule = lane_for(signal)
    cap = limit_price(signal)[1]
    qty, cost = D(0), D(0)
    best = None
    worst = None
    for bid, n in sorted(levels.items(), reverse=True):
        price = 1-bid
        if n <= 0:
            continue
        if best is None:
            best = price
        if price < rule.floor:
            return dict(ok=False, reason='below_floor', best=float(price), qty=0)
        if price > cap:
            break
        take = min(D(rule.contracts)-qty, n)
        qty += take; cost += take*price; worst = price
        if qty == rule.contracts:
            break
    return dict(ok=qty == rule.contracts, reason='valid' if qty == rule.contracts else 'price_depth',
                best=float(best) if best is not None else None, qty=float(qty),
                average=float(cost/qty) if qty else None, worst=float(worst) if worst is not None else None)

def main():
    data = json.loads((P/'inputs.json').read_text())
    grouped = collections.defaultdict(list)
    for e in data['events']:
        if e.get('asset') in ('BTC','ETH') and e.get('z',0) >= (3 if e.get('lane') == 'premium_z3' else 2):
            grouped[(e['ticker'],e['lane'],e['decision_ts'])].append(e)
    signals=[]
    for key, events in grouped.items():
        s=events[0]
        signals.append(dict(key=list(key),signal=s,ready_ms=int(s['event_ts']*1000)+1,
                            last_check_age=events[-1]['event_ts']-s['decision_ts'],
                            checks=len(events),states=[],coverage_start=None,coverage_end=None))
    by_ticker=collections.defaultdict(list)
    for s in signals:by_ticker[s['key'][0]].append(s)
    manifests=[];anomalies=collections.Counter()
    for filename in data['files']:
        path=Path(filename);close=int(path.name.split('_')[1])
        relevant=[s for s in signals if s['signal']['close_ts']==close]
        if not relevant:continue
        books={};seq={};invalid=set();last_ms={};last_send={};known=set();n=0
        with gzip.open(path,'rt') as inp:
            for line in inp:
                if '"type":"orderbook_' not in line and '"type":"capture_' not in line:continue
                r=json.loads(line);kind=r.get('type');m=r.get('msg',{});ticker=m.get('market_ticker');now=r['client_received_at_ms']
                if kind=='capture_sequence_gap':
                    anomalies['recorded_sequence_gap']+=1;invalid.update(books);continue
                if kind=='capture_error':
                    anomalies['capture_error']+=1;invalid.update(books);continue
                if kind not in ('orderbook_snapshot','orderbook_delta'):continue
                sid=r.get('sid');q=r.get('seq');prior=seq.get(sid);seq[sid]=q
                if prior is not None and q!=prior+1:
                    anomalies['sequence_gap']+=1;invalid.update(books)
                if kind=='orderbook_snapshot':
                    books[ticker]={side:{D(p):D(n) for p,n in m.get(side+'_dollars_fp',[]) if D(n)>0} for side in ('yes','no')}
                    invalid.discard(ticker);known.add(ticker)
                elif ticker not in books:
                    anomalies['delta_before_snapshot']+=1;continue
                else:
                    side=m['side'];price=D(m['price_dollars']);qty=books[ticker][side].get(price,D(0))+D(m['delta_fp'])
                    if qty<0:anomalies['negative_depth']+=1;invalid.add(ticker)
                    if qty<=0:books[ticker][side].pop(price,None)
                    else:books[ticker][side][price]=qty
                last_ms[ticker]=now;last_send[ticker]=r.get('sending_ts_ms',now);n+=1
                for s in by_ticker.get(ticker,[]):
                    begin=s['signal']['decision_ts']*1000
                    if not begin<=now<=begin+36000:continue
                    if s['coverage_start'] is None:s['coverage_start']=now
                    s['coverage_end']=now
                    side='no' if s['signal']['side']=='yes' else 'yes'
                    state=book_state(books[ticker][side],s['signal']) if ticker not in invalid else dict(ok=False,reason='invalid_book')
                    if not 0<=now-last_send[ticker]<=1000:state=dict(ok=False,reason='stale_source')
                    value=dict(received_ms=now,**state)
                    # Keep every quality/price transition. Equal states extend the prior interval.
                    if not s['states'] or {k:v for k,v in s['states'][-1].items() if k not in ('received_ms','last_ms')}!=state:
                        s['states'].append(value)
                    s['states'][-1]['last_ms']=now
        manifests.append(dict(file=filename,bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),book_frames=n))
        print(path.name,n,flush=True)
    actual={o['ticker']:o for o in data['orders'] if float(o.get('filled',0))>0}
    for s in signals:
        T=s['signal']['decision_ts']*1000
        s['actual_filled']=s['key'][0] in actual
        s['actual_status']=s['signal'].get('execution_status') or s['signal']['status']
        s['same_rules']={}
        for deadline in (25,35):
            for delay in (0,100,250,500):
                found=None
                for i,r in enumerate(s['states']):
                    if r['received_ms']<s['ready_ms'] and (i+1<len(s['states']) and s['states'][i+1]['received_ms']<=s['ready_ms']):continue
                    # Stop at an observed weakened book, preserving terminal-floor behavior.
                    if r.get('reason')=='below_floor' and r.get('last_ms',r['received_ms'])>=s['ready_ms']:break
                    if not r['ok']:continue
                    at=max(r['received_ms'],s['ready_ms'])
                    until=s['states'][i+1]['received_ms'] if i+1<len(s['states']) else r.get('last_ms',r['received_ms'])
                    # Conservative continuous-price/depth support across the whole delay.
                    if at<=T+deadline*1000 and at+delay<until and at+delay<=T+35000:
                        found=dict(at_ms=at,age=(at-T)/1000,delay_ms=delay,average=r.get('average'),worst=r.get('worst'),continuous_ms=until-at)
                        break
                s['same_rules'][f'deadline{deadline}_delay{delay}']=found
    out=dict(start=data['start'],end=data['end'],signals=signals,manifests=manifests,anomalies=dict(anomalies),
             note='Observed displayed opportunities, not guaranteed fills. Same captured frozen signals; no reconstruction of missing qualifying signals. Readiness conservatively after first completed REST attempt/event. No budget or account state bypass is implied.')
    (P/'stream_replay.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in s.items() if k not in ('states','signal')} for s in signals],indent=2))

if __name__=='__main__':main()
