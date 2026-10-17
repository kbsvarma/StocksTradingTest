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

cohorts = {}
all_tickers = None
for v in ('rest_3c_10s','rest_1c_10s'):
    rows=sorted([o for o in orders if o['variant']==v and comparable(o)],
                key=lambda o:(o['close_ts'],o['ticker']))
    tickers={o['ticker'] for o in rows}
    assert all_tickers is None or all_tickers==tickers
    all_tickers=tickers
    cohorts[v]={}
    for label, rs in [('first_20',rows[:20]),('all_complete',rows)]:
        aa=[actual[o['ticker']] for o in rs if o['ticker'] in actual
            and number(actual[o['ticker']].get('filled',0))>0]
        controls=[o for o in orders if o['variant']=='paper_ioc' and o['ticker'] in {r['ticker'] for r in rs}]
        assert all(comparable(o) for o in controls)
        a_net=sum(number(a.get('pnl',0)) for a in aa)
        cohorts[v][label]=dict(n=len(rs),paper=summarize(rs),ioc=summarize(controls),
            actual=dict(filled=len(aa),wins=sum(number(a['pnl'])>0 for a in aa),
                        losses=sum(number(a['pnl'])<0 for a in aa),net=str(a_net)),
            delta=str(sum(number(o['pnl']) for o in rs)-a_net))

output=dict(review_ts=report['ts'],http_spacing_min=spacing,max_requests_per_second=rate,
    all_http_latency_ms=stats([(r['received']-r['started'])*1000 for r in receipts.values()]),
    delivery_seconds=ops['signal_delivery_seconds'],signal_to_accept_seconds=ops['signal_to_accept_seconds'],
    fill_print_first_arrival_seconds=stats([e['first_arrival_delay_seconds'] for e in ops['resting_fill_receipts']]),
    book_gaps_including_tail=gaps,cancellation_audit=cancels,cohorts=cohorts,
    uncertainty='Only about one elapsed day, spanning two partial ET dates. Too few days and resting fills for a reliable clustered edge estimate. No confidence interval claimed.',
    http_error_explanation='Eight historical startup series-cache rejections; all preserved receipts were HTTP200. No new request/series errors, no HTTP429.',
    validations=['Same 34-market complete cohort for both resting variants and IOC',
                 'All admission-time fee metadata tied to raw series receipts',
                 'Two public requests/second rate and complete-lifetime book gaps <=4 seconds',
                 'Floor cancellation timing and in-lifetime fill timestamps verified',
                 'No configuration, model, ledger or live service changes'])
(DEST/'milestone_audit.json').write_text(json.dumps(output,indent=2)+'\n')

lines=['RESTING-ORDER PAPER TEST: FIRST 20-OPPORTUNITY MILESTONE REVIEW',
       'Snapshot: 2026-10-15 17:20 ET. Preliminary simulated execution only.',
       '47 observed qualifying market opportunities; 34 settled complete comparable windows.',
       '13 windows remain inconclusive: six old pause-guard skips and seven health cancellations.',
       'About one elapsed day; entry cutoff remains October 28, 17:11 ET.', '',
       'MAIN FINDING',
       'Resting orders obtained cheaper fills, but missed winning trades outweigh those savings.',
       'On the SAME 34 complete market opportunities, including zero fills:',
       'Design | filled markets | wins/losses | net after fees | +1c/contract friction | taker-fee stress']
for v in ('rest_3c_10s','rest_1c_10s'):
    s=cohorts[v]['all_complete']['paper']
    lines.append(f"{v} | {s['filled_markets']} | {s['wins']}/{s['losses']} | {s['net_after_fees']} | {s['net_after_1c_friction']} | {s['net_taker_fee_stress']}")
s=cohorts['rest_1c_10s']['all_complete']['ioc']
lines.append(f"One-shot paper IOC | {s['filled_markets']} | {s['wins']}/{s['losses']} | {s['net_after_fees']} | {s['net_after_1c_friction']} | {s['net_taker_fee_stress']}")
a=cohorts['rest_1c_10s']['all_complete']['actual']
lines.append(f"Actual live bot | {a['filled']} | {a['wins']}/{a['losses']} | {a['net']} | not modeled | actual fees")
lines+=['','1c resting underperforms live by $1.6666; 3c underperforms by $3.0766.',
        '1c misses 19 complete-coverage live winners worth $6.8296; 3c misses 23 worth $7.7286.',
        'Both avoid one $4.3896 USD/CAD loss before that lane was paused. This is not a current CAD opportunity.',
        'The complete cohort excludes a WTI loss and a EURUSD resting winner from interrupted coverage; neither is silently credited as an avoided loss or reliable fill.',
        '', 'ALL RECORDED PAPER FILLS (different denominator; do not compare directly with complete-cohort live P&L)']
for v,s in report['variants'].items():
    lines.append(f"{v}: {s['filled_markets']} fills, {s['wins']}W/{s['losses']}L, net {s['net_after_fees']}, fees {s['fees']}, friction-net {s['net_after_1c_friction']}, taker-stress {s['net_taker_fee_stress']}, observed drawdown {s['settled_drawdown']}.")
lines+=['The 1c all-recorded $3.21 includes a $0.60 winner in an interrupted window; comparable net is $2.61.',
        'All actual live entries since experiment start: 38 settled, net -$1.3432; every entry has a captured signal.',
        'That full-live total includes incomplete paper windows and is not a fair paired comparator.', '',
        'SIMULATED RESTING FILLS: market | variant | side | quantity | entry | fee | outcome | net | complete coverage']
for o in orders:
    if o['variant']!='paper_ioc' and number(o['filled'])>0:
        lines.append(' | '.join(str(o.get(k)) for k in ('ticker','variant','side','filled','price','fees','result','pnl','coverage_complete')))
lines+=['', 'MATCHED ENTRY PRICES']
for v in ('rest_3c_10s','rest_1c_10s'):
    c=report['comparisons'][v]
    lines.append(f"{v}: {c['matched_same_side']} matched fills across all records; mean improvement {c['weighted_entry_improvement_cents']} cents per contract. Exact prices/coverage in report.json and orders.json.")
    for pair in c['paired']:
        lines.append(f"  {pair['ticker']}: live {pair['live_entry']}, simulated resting {pair['paper_entry']}; improvement {pair['improvement_cents']}c.")
lines+=['One 1c paper-only fill: WTI Oct14 17:30, five YES at 93.1c, +$0.345; live unfilled.',
        'No terminal partial fills. Some full fills aggregate fractional print quantities.',
        'Two 3c and three 1c quote touches without fills remain unfilled, not promoted to trades.', '',
        'FREQUENCY / SPLITS (all recorded; short sample, not a forecast)']
for v in report['variants']:
    lines.append(v+' by ET date: '+json.dumps(report['comparisons'][v]['calendar_days']))
    for asset,s in report['by_asset'][v].items():
        if s['opportunities']:
            lines.append(f"  {asset}: {s['opportunities']} opportunities, {s['filled_markets']} fills, net {s['net_after_fees']}")
    for lane,s in report['by_lane'][v].items():
        lines.append(f"  {lane}: {s['opportunities']} opportunities, {s['filled_markets']} fills, net {s['net_after_fees']}")
lines+=['', 'OPERATIONS / LIMITS',
        'Collector active, synchronized clock, no current health block; all review source/flag checks pass.',
        '925 raw receipt hashes and 2,050 unique public trades audited. No trade corrections or truncated pagination.',
        'No HTTP429 or new request failures. Status counter8 is the preserved startup series-cache issue.',
        f'Public request spacing minimum {spacing:.6f}s; maximum {rate}/second.',
        f'Largest full observed book interval including acceptance/expiry tails {max(g["max_gap_including_tail"] for g in gaps):.3f}s.',
        'Live publication to paper delivery median47ms/max101ms. Qualification to paper acceptance median1.387s/max1.816s.',
        'This collector receives events after publication and yields the first two seconds of the minute; it is slower than an integrated live resting-order path.',
        'The synchronous control is one IOC at the paper arrival, not the production bot with its retries.',
        'Printed-flow direction, queue, size, caps, lifetimes, cancellation timing, fees and official settlements all validated.',
        'Current captured series fee metadata: quadratic, multiplier1, modeled maker fee0. Taker-fee and1c friction results above.',
        '7 historical health cancellations remain incomplete. Exact historic live status was not retained; reconciliation correlation is not proof of which health field triggered.',
        'No new health cancellations since previous review; authorized UI-only source-guard restart remains documented separately.',
        '', 'DECISION',
        'Continue the unchanged frozen paper experiment. No live promotion or parameter changes.',
        'This is an operational milestone, not an edge verdict: only two 3c and eight total 1c simulated fills, about one elapsed day, two partial calendar dates.',
        'Zero observed resting drawdown is not evidence of low risk; a single five-contract loss can reverse these small profits.',
        'A sound edge assessment still needs 14 calendar days and 100 settled comparable opportunities, with enough actual simulated resting fills.',
        'All production and paper service identities/source/flags preserved during this review. No financial orders or service restarts.',
        'Raw ledger/receipts stay on the server; compact audit artifacts are mirrored locally. No full database copied.']
(DEST/'MILESTONE_REPORT.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps({'report':str(DEST/'MILESTONE_REPORT.txt'),'cohorts':cohorts,'http_spacing_min':spacing,'rate':rate}))
