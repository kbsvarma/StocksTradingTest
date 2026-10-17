"""Milestone reporting only. Read frozen review files and read-only receipts."""
import bisect
import datetime as dt
import gzip
import json
import sqlite3
import statistics
import sys
from decimal import Decimal as D
from pathlib import Path

DEST = Path(__file__).resolve().parent
ROOT = DEST.parent.parent
sys.path.insert(0, str(ROOT))
from paper import book, lane_for, number
from review import summarize

report = json.loads((DEST/'report.json').read_text())
orders = json.loads((DEST/'orders.json').read_text())
actual = json.loads((DEST/'actual.json').read_text())
ops = json.loads((DEST/'operations.json').read_text())
db = sqlite3.connect((ROOT/'paper.sqlite').as_uri()+'?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
receipts = {i: dict(json.loads(gzip.decompress(raw)), id=i) for i, raw in db.execute(
    'SELECT id,record FROM receipts WHERE id<=?', (ops['receipt_hashes_verified'],))}
db.close()
assert len(receipts) == ops['receipt_hashes_verified']

def stats(xs):
    xs = sorted(xs)
    return dict(n=len(xs), median=statistics.median(xs) if xs else None,
                p95=xs[min(len(xs)-1, int(.95*len(xs)))] if xs else None,
                max=max(xs) if xs else None)

starts = sorted(r['started'] for r in receipts.values())
spacing = min(b-a for a,b in zip(starts,starts[1:]))
rate = max(bisect.bisect_left(starts, t+1)-i for i,t in enumerate(starts))
assert spacing >= .499 and rate <= 2
gaps, cancels = [], []
for o in orders:
    if not o.get('admission_receipt'):
        continue
    if o.get('price') is not None:
        assert o['effective_expiry'] <= o['original_expiry'] <= o['accepted_ts']+10.001
        assert o['original_expiry'] <= o['signal']['decision_ts']+25.001
    meta = o['fee_meta']
    src = receipts[meta['receipt']]['body']['series']
    assert meta['fee_type'] == src['fee_type']
    assert number(meta['multiplier']) == number(src['fee_multiplier'])
    assert 0 <= o['created_ts']-meta['received'] <= 25200
    if o['variant'] == 'paper_ioc' or o['state'] == 'skipped':
        continue
    start, end = o['accepted_ts'], o['effective_expiry']
    path = '/markets/'+o['ticker']+'/orderbook'
    books = [r for r in receipts.values() if r['path']==path]
    times = [start]+sorted(r['received'] for r in books if start<r['received']<end)+[max(start,end)]
    gap = max((b-a for a,b in zip(times,times[1:])),default=0)
    gaps.append(dict(variant=o['variant'],ticker=o['ticker'],max_gap_including_tail=gap,
                     complete=o['coverage_complete']))
    if o['coverage_complete']:
        assert gap <= 4
    if o.get('end_reason') == 'floor_cancel_requested':
        breaches = [r for r in books if r['received']>=start and
                    book(r['body'],o['side'])[2] is not None and
                    book(r['body'],o['side'])[2] < lane_for(o['signal']).floor]
        first = min(breaches,key=lambda r:r['received'])
        assert abs(end-min(o['original_expiry'],first['received']+.25))<.001
        cancels.append(dict(variant=o['variant'],ticker=o['ticker'],
            first_breach_receipt=first['id'],first_breach_received=first['received'],
            cancel_effective=end,filled=o['filled'],
            prints_during_cancel_race=sum(first['received']<=e['exchange_ts']<end
                                         for e in o['fill_evidence'])))

def comparable(o):
    a=actual.get(o['ticker'],{})
    return (o.get('outcome_recorded') and o.get('coverage_complete') and
            (not number(a.get('filled',0)) or a.get('status')=='settled'))

variants = ('rest_3c_10s', 'rest_1c_10s', 'paper_ioc')
complete = {v: {o['ticker'] for o in orders if o['variant']==v and comparable(o)} for v in variants}
common = set.intersection(*(complete[v] for v in variants))

def cohort(tickers):
    aa = [actual[t] for t in tickers if t in actual and number(actual[t].get('filled',0))>0]
    a_net = sum(number(a.get('pnl',0)) for a in aa)
    return dict(n=len(tickers), tickers=sorted(tickers),
        actual=dict(filled=len(aa), wins=sum(number(a['pnl'])>0 for a in aa),
            losses=sum(number(a['pnl'])<0 for a in aa), net=str(a_net)),
        variants={v: summarize([o for o in orders if o['variant']==v and o['ticker'] in tickers])
                  for v in variants})

cohorts = {v: cohort(complete[v]) for v in variants}
common_cohort = cohort(common)
assert all(common_cohort['variants'][v]['unknown_coverage']==0 for v in variants)
for v in variants:
    assert common_cohort['variants'][v]['opportunities']==len(common)
elapsed = (report['ts']-report['started'])/86400
output=dict(review_ts=report['ts'], http_spacing_min=spacing,max_requests_per_second=rate,
    all_http_latency_ms=stats([(r['received']-r['started'])*1000 for r in receipts.values()]),
    delivery_seconds=ops['signal_delivery_seconds'],signal_to_accept_seconds=ops['signal_to_accept_seconds'],
    fill_print_first_arrival_seconds=stats([e['first_arrival_delay_seconds'] for e in ops['resting_fill_receipts']]),
    book_gaps_including_tail=gaps,cancellation_audit=cancels,per_variant_cohorts=cohorts,
    common_complete_cohort=common_cohort,
    uncertainty=f'{elapsed:.3f} elapsed days; below frozen 14-day horizon. No confidence interval or edge claim.',
    validations=['Common cohort requires complete coverage for all three variants; individual-cohort controls may be incomplete',
      'Admission-time fees, original caps, queue, cancellation races verified',
      'Public requests <=2/sec; complete-lifetime book gaps <=4s','No model/config/ledger/live changes'])
(DEST/'review_checks.json').write_text(json.dumps(output,indent=2)+'\n')
print(json.dumps({'common':common_cohort,'spacing':spacing,'rate':rate,
    'max_gap':max(g['max_gap_including_tail'] for g in gaps)}))
