"""Gold execution-price check using original, timestamped archived quotes.

There is NO displayed quantity in this archive: these are price opportunities,
never proof of a five-contract fill. No new signal or threshold is optimized.
"""
import bisect
from collections import Counter, defaultdict
import gzip
import json
import pickle
import replay as r

def main():
    markets = pickle.loads((r.P/'features.pickle').read_bytes())
    markets = [m for m in markets if m['asset']=='GOLD']
    by = {m['ticker']:m for m in markets}
    quotes = defaultdict(dict)
    counts = Counter()
    with gzip.open(r.P/'recorded_quotes.jsonl.gz','rt') as f:
        for line in f:
            q = json.loads(line)
            counts['input_rows'] += 1
            m = by.get(q['ticker'])
            if not m or not m['start']<=q['ts']<m['close']:
                counts['outside_market_window'] += 1
                continue
            try:
                b, a = float(q['bid']), float(q['ask'])
            except (KeyError,TypeError,ValueError):
                counts['missing_price'] += 1
                continue
            if not 0<=b<=a<=1:
                counts['invalid_price'] += 1
                continue
            t = float(q['ts'])
            if t in quotes[q['ticker']] and quotes[q['ticker']][t]!=(b,a):
                raise ValueError('conflicting same-time quotes')
            quotes[q['ticker']][t] = (b,a)
    points = {k:sorted(v.items()) for k,v in quotes.items()}
    timestamps = {k:[v[0] for v in vv] for k,vv in points.items()}
    out, ledger = {}, {}
    for variant, retry, latency in [('first_observed',False,3), ('bounded_rechecks',True,3), ('ten_second_start',True,10)]:
        candidates = []
        skips = Counter()
        for m in markets:
            qs = points.get(m['ticker'],[])
            times = timestamps.get(m['ticker'],[])
            for f in m['features']:
                done_at = f['t']+latency
                for lane in (r.Z2,r.Z3):
                    if not r.qualifies(f,lane):
                        continue
                    rule = r.LANES[lane]
                    cap = float(r.limit_price(dict(lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges']))[1])
                    j = bisect.bisect_left(times, done_at)
                    attempts = 0
                    accepted = False
                    while j < len(qs):
                        t,(b,a) = qs[j]
                        if t-f['t']>35:
                            break
                        attempts += 1
                        entry = a if f['side']==1 else 1-b
                        if entry < float(rule.floor)-1e-8:
                            skips['weakened_below_floor'] += 1
                            done_at = t+.3
                            break
                        if entry <= cap+1e-8:
                            fee = r.fees(entry)
                            won = int(f['side']==m['result'])
                            candidates.append(dict(ticker=m['ticker'], asset='GOLD',lane=lane,t=f['t'],
                                entered=t,close=m['close'],settled=m['settled'],side=f['side'],
                                entry=entry,cap=cap,quantity=5,fee=fee,won=won,pnl=5*(won-entry)-fee,
                                ask=f['ask'],bid=f['bid'],z=f['z'],rem=f['rem'],mode=variant))
                            accepted = True
                            break
                        if not retry or attempts>=8 or t-f['t']>25:
                            done_at = t+.3
                            break
                        j += 1
                    else:
                        skips['no_next_observation'] += 1
                    if accepted:
                        break  # z2 first, one assumed submission per market/minute
                    if j==len(qs) or (j<len(qs) and qs[j][0]-f['t']>35):
                        done_at = f['t']+36
        blocks = {}
        for period, start, end in r.windows():
            selected = [x for x in candidates if start<=x['close']<end]
            block = {}
            for stress in (0,.01,.02):
                trades, rejected = r.portfolio(selected,stress)
                block[str(stress)] = dict(r.metrics(trades),blocked=rejected)
                ledger[f'{variant}/{period}/{stress}'] = trades
            blocks[period] = block
        out[variant] = dict(periods=blocks,skip_counts=dict(skips),candidate_minutes=len(candidates))
    alltimes = [t for tt in timestamps.values() for t in tt]
    out['coverage'] = dict(counts,markets=len(points),first_ts=min(alltimes),last_ts=max(alltimes),
                           has_orderbook_quantity=False,
                           limitation='Recorded quote timestamps establish observed prices, not source publication age, queue priority, or five-contract depth.')
    r.save('recorded_quote_results.json',out)
    with gzip.open(r.P/'recorded_quote_trade_ledgers.json.gz','wt') as f:
        json.dump(ledger,f)
    print(json.dumps({k:{p:x['0.01']['net'] for p,x in v['periods'].items()} for k,v in out.items() if k!='coverage'}))
    print(json.dumps(out['coverage']))

if __name__=='__main__':
    main()
