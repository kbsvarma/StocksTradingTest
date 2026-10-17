import sys,json,sqlite3
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
from operations_view import read_funnel
from observe_execution import exact_cap,book_stats
from lanes import lane_for
A=Path('/home/varma/kalshi_operations_audit_2026_10_05')
start=json.loads((A/'z3_receipt.json').read_text())['verified_ts']
funnel=read_funnel(A/'snapshot.sqlite3',start)
conn=sqlite3.connect((A/'snapshot.sqlite3').as_uri()+'?mode=ro',uri=True)
signals={}
for raw, in conn.execute('select event from events where ts>=?',(start,)):
    e=json.loads(raw)
    if e.get('lane')=='premium_z3' and e.get('z',0)>=3 and 'price_ranges' in e:
        signals[(e['ticker'],e['decision_ts'])]=e
checks=passes=wrong=0
for line in (A/'observer.jsonl').read_text().splitlines():
    r=json.loads(line);s=signals.get((r.get('ticker'),r.get('decision_ts')))
    if not s or 'levels' not in r:continue
    checks+=1;ok=book_stats(r['levels'],exact_cap(s),5,float(lane_for(s).floor))['enough']
    passes+=ok;wrong+=ok and not r['depth']['5']['enough']
result=dict(funnel=funnel,z3_observer_checks=checks,correct_passes=passes,previously_wrong=wrong)
(P/'diagnostics_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
z3=next(r for r in funnel if r['lane']=='premium_z3')
assert (z3['qualified'],z3['filled'],z3['zero_fill'],z3['risk_blocked'],z3['book_blocked'])==(11,5,1,2,3)
assert (checks,passes,wrong)==(96,46,46)
