"""Audit saved research accounting and chronology; never touches live bots."""
import json,math
from pathlib import Path
P=Path(__file__).resolve().parent
count=0
for prefix in ['rain_replay','rain_intraday']:
    results=json.load(open(P/(prefix+'_results.json')))
    ledgers=json.load(open(P/(prefix+'_ledgers.json')))
    for key,rows in ledgers.items():
        r=results[key]
        assert len(rows)==r['trades']
        assert len({v['owner'] for v in rows})==len(rows)
        assert abs(sum(v['net'] for v in rows)-r['net'])<1e-5
        for v in rows:
            assert v['source_t']<v['t']<v['entered']<v['settled']
            assert v['qty']==5 and v['entry']<=v['cap']+1e-9 and v['cap']<=.97+1e-9
            assert v['volume10']>=5 and v['entry']>=max(v['signal_ask'],v['next_ask'])-1e-9
            assert abs(v['net']-(5*v['payout']-5*(v['entry']+v['friction'])-v['fee']))<1e-8
        count+=1
pairs=json.load(open(P/'rain_pair_results.json'))
for p in pairs:
    if 'net_floor_after_friction' in p:
        a,b=p['day_no'],p['weekend_yes']
        assert abs(sum(q for q,price in a['fills'])-5)<1e-9
        assert abs(sum(q for q,price in b['fills'])-5)<1e-9
        assert abs(p['net_floor_after_friction']-(5-a['cost']-b['cost']-a['fees']-b['fees']-.1))<1e-9
print(f'PASS: {count} scenario ledgers, chronology, caps, accounting; {len(pairs)} rain pairs.')
