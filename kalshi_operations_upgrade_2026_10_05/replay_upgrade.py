"""Replay exact captured book deltas through the new parser/scheduler; no orders."""
import sys,json,gzip,time
from pathlib import Path
from decimal import Decimal
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
from book_feed import Books,CheckSchedule
from price_rules import limit_price
from lanes import lane_for
D=Path('/home/varma/kalshi_operations_audit_2026_10_05')
data=json.loads((D/'stream_inputs.json').read_text())
s=next(e for e in data['events'] if e.get('decision_ts')==1791090720 and e.get('lane')=='premium_z3' and e.get('z',0)>=3)
T=s['decision_ts'];ticker=s['ticker'];rule=lane_for(s);cap=limit_price(s)[1]
books=Books();schedule=CheckSchedule();checks=0;prior=False;hint=None;triggers=[];crossings=[];frames=0;errors=[]
path=next(Path(f) for f in data['files'] if 'capture_1791090900_' in f)
with gzip.open(path,'rt') as inp:
    for line in inp:
        if '"type":"orderbook_' not in line:continue
        r=json.loads(line);now=r['client_received_at_ms']/1000
        try:changed=books.apply(r,now)
        except Exception as e:errors.append(str(e));continue
        frames+=1
        if now<T or now>T+25 or changed!=ticker:continue
        ready=books.ready(ticker,s['side'],rule.floor,cap,5,now)
        if ready and not prior:
            hint=(books.versions[ticker],now);crossings.append(round(now-T,6))
        prior=ready
        if now<s['event_ts']:continue
        reason=schedule.ready(ticker,checks,now-T,now,hint if ready else None)
        if reason:
            schedule.checked(ticker,now,hint if ready else None,reason);checks+=1
            triggers.append(dict(age=round(now-T,6),reason=reason,displayed_depth_valid=ready))
result=dict(signal={k:s[k] for k in ('ticker','decision_ts','z','signal_ask','side')},cap=str(cap),frames=frames,errors=errors,
            crossings=crossings,checks=checks,triggers=triggers,
            note='Scheduling replay of recorded received frames and frozen signal. REST/account latency, risk and exchange fills are not simulated; no guaranteed incremental fills or P&L.')
(P/'replay_upgrade.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
assert not errors
assert any(r['reason']=='book_update' and r['displayed_depth_valid'] for r in triggers)
