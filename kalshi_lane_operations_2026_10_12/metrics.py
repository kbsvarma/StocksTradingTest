"""Independent checks of frozen receipts; no production imports or writes."""
import collections
from decimal import Decimal as D
import gzip
import json
from pathlib import Path
import statistics
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'source'))
from lanes import lane_for
from price_rules import limit_price
summary=json.loads((P/'summary.json').read_text())
start=int(datetime.fromisoformat(summary['window_start_et']).timestamp())
end=int(summary['ts']//60)*60
events=[e for e in json.load(gzip.open(P/'events.json.gz','rt')) if start<=e['decision_ts']<end]
attempts=[r for r in json.load(gzip.open(P/'attempts.json.gz','rt')) if start<=r['created_ts']<end]
orders=[r for r in json.load(gzip.open(P/'orders.json.gz','rt')) if start<=r['created_ts']<end]
misses=json.loads((P/'miss_classification.json').read_text())
proof=[]
for r in attempts:
    s=r['signal'];rule=lane_for(s);cap=limit_price(s)[1]
    checks=dict(z=s['z']>=rule.threshold(s['remaining_minutes']),
                price=float(rule.floor)-1e-8<=s['signal_ask']<float(rule.ceiling),
                spread=s['signal_ask']-s['signal_bid']<=.02000001,
                time=rule.min_minutes<=s['remaining_minutes']<=rule.max_minutes,
                fresh=0<=r['created_ts']-s['decision_ts']<=35,
                size=D(r['requested_count'])==5,
                cap=D(r['favorite_limit'])==cap,
                cost=D(r.get('cost','0'))<=D(r['filled'])*cap,
                executable=s.get('execution_executable') is True)
    assert all(checks.values()),(r['ticker'],r['attempt_number'],checks)
    proof.append(dict(ticker=r['ticker'],attempt=r['attempt_number'],checks=checks))

explicit={e['ticker'] for e in events if e.get('execution_executable')}
submitted={r['ticker'] for r in attempts if r.get('order_id')}
assert not explicit-submitted
assert not any(r['valid_within_retry'] or r['valid_after_retry'] for r in misses.values())
errors=[e for e in events if e['status']=='data_error']
keys=collections.defaultdict(list)
for e in events:keys[e['asset'],e['lane'],e['decision_ts']].append(e)
recovered={k for k,es in keys.items() if any(e['status']=='data_error' for e in es) and es[-1]['status']!='data_error'}
errors_after_ui=[e for e in errors if e['decision_ts']>=1791823320]
inputs=json.loads((P/'input_failures.json').read_text())
unresolved=[{k:v for k,v in r.items() if k!='trace'} for r in inputs if r['last'] in ('decision_candle_unavailable','incomplete_spot','no_decision_quote','data_error')]
def stats(v):
    return dict(n=len(v),median=statistics.median(v) if v else None,max=max(v) if v else None)

out=dict(start=start,end=end,attempt_checks=proof,explicit_executable_markets=len(explicit),
         executable_unsubmitted=sorted(explicit-submitted),
         recorded_book_snapshots_for_misses=sum(r['snapshots'] for r in misses.values()),
         executable_miss_snapshots=0,error_events=len(errors),rate_limit_events=sum('429' in e.get('error','') for e in errors),
         other_errors=[e.get('error') for e in errors if '429' not in e.get('error','')],
         recovered_error_minutes=len(recovered),error_events_after_ui=len(errors_after_ui),
         unresolved_inputs=unresolved,
         latency=dict(account_ms=stats([e['execution_account_ms'] for e in events if e.get('execution_account_ms') is not None]),
                      preliminary_to_final_book_ms=stats([e['execution_book_gap_ms'] for e in events if e.get('execution_book_gap_ms') is not None]),
                      final_book_to_intent_ms=stats([1000*(r['created_ts']-r['signal']['execution_book_ts']) for r in attempts]),
                      signal_to_intent_seconds=stats([r['created_ts']-r['signal']['decision_ts'] for r in attempts])),
         filled_on_later_check=sum(r['signal'].get('execution_check_number',1)>1 for r in orders),
         recovered_zero_fill_markets=[r['ticker'] for r in orders if r.get('attempt_number',1)>1 and D(r['filled'])>0])
(P/'metrics.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('attempt_checks','unresolved_inputs')},indent=2))
