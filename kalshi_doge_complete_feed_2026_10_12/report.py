"""Render frozen replay outputs, including adverse cases, into review artifacts."""
import collections as C
import datetime as dt
import gzip
import json
import math
from pathlib import Path
import random
import statistics
P=Path(__file__).resolve().parent
R=json.loads((P/'results.json').read_text())
L=json.load(gzip.open(P/'ledgers.json.gz','rt'))
DEC=json.loads((P/'decomposition.json').read_text())
COV=json.loads((P/'coverage.json').read_text())
IC=json.loads((P/'index_coverage.json').read_text())
SI=json.loads((P/'settlement_integrity.json').read_text())
VALID=json.loads((P/'validation.json').read_text())

def m(feed,scenario='plus1c',period='full'):
    return R[feed][scenario]['periods'][period]
def row(label,x):
    return f"{label} | {x['trades']} | {x['wins']}/{x['losses']} | {x['net']:+.4f} | {x['settled_drawdown']:.4f}"
def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat()

# Small-sample diagnostics; not a selection objective or an evidence threshold.
stats={}
for feed in ('coinbase','cf_close','cf_opening_average','cf_common_coverage'):
    rows=L[feed]['plus1c']['full']; n=len(rows); wins=sum(x['won'] for x in rows)
    if n:
        z=1.959963984540054;phat=wins/n;den=1+z*z/n
        center=(phat+z*z/(2*n))/den;half=z*math.sqrt(phat*(1-phat)/n+z*z/(4*n*n))/den
        stats[feed]={'win_rate':phat,'wilson_95':[center-half,center+half],
            'mean_entry':statistics.mean(x['entry'] for x in rows),
            'average_fee_and_1c_break_even_probability':statistics.mean(x['entry']+x['fee']/5+.01 for x in rows),
            'losses':[x for x in rows if not x['won']]}

# Calendar-day paired resampling includes no-trade dates. Report exploratory,
# since there are only twelve UTC date buckets, one of which is partial.
start=dt.datetime(2026,10,1,tzinfo=dt.timezone.utc).timestamp()
end=dt.datetime(2026,10,12,4,30,tzinfo=dt.timezone.utc).timestamp()
days=[];t=start
while t<end:
    hi=min(t+86400,end);days.append((day(t),(hi-t)/86400));t=hi
old=C.defaultdict(float);new=C.defaultdict(float)
for x in L['coinbase']['plus1c']['full']:old[day(x['close'])]+=x['net']
for x in L['cf_close']['plus1c']['full']:new[day(x['close'])]+=x['net']
rng=random.Random(12102026);totals=[];delta=[];duration=(end-start)/86400
for _ in range(10000):
    sample=[rng.choice(days) for _ in days];exposure=sum(v for _,v in sample)
    totals.append(sum(new[d] for d,_ in sample)/exposure*duration)
    delta.append(sum(new[d]-old[d] for d,_ in sample)/exposure*duration)
stats['exploratory_utc_day_bootstrap']={'replicates':10000,'date_buckets':len(days),
    'cf_net_equivalent_period_95':[sorted(totals)[250],sorted(totals)[9749]],
    'paired_net_difference_equivalent_period_95':[sorted(delta)[250],sorted(delta)[9749]],
    'limitations':'Few dependent days and same research sample; descriptive only, not proof of future profitability.'}
(P/'uncertainty.json').write_text(json.dumps(stats,indent=2)+'\n')

lines=['DOGE COMPLETE-FEED REPLAY — 12 OCTOBER 2026',
 'Research only. Live code, services, flags, risk and traded assets are unchanged.',
 '', 'FINDING',
 'The feed bottleneck is real: missing primary minutes fall from 980 to 0; complete decision observations rise from 2,738 to 8,464.',
 'Unchanged two-lane rules produce 27 trades versus 11 (+16, +145%); 26 wins/1 loss versus 11 wins.',
 'More trades do not improve the primary net: fees-only +$2.5701 vs +$2.9948; fees + 1c cost +$1.2201 vs +$2.4448.',
 'At 2c friction the complete-feed two-lane result is -$0.1299. The evidence does not justify deploying that version.',
 'Exploratory lane-isolation replay below identifies DOGE z2-only as the better prospective paper candidate.',
 'That lane choice was examined AFTER seeing these results, so it needs new forward evidence; no live change.',
 '', 'FROZEN COMPARISON',
 'Oct1 00:00UTC <= market close < Oct12 04:30UTC (11.1875 elapsed calendar days).',
 '1,058 settled DOGE markets; same frozen prediction quotes and outcomes on both feeds.',
 'Sixteen quarter-hour slots have no returned markets: Oct1 and Oct8 07:15–09:00UTC.',
 'Bounded all-status public queries returned no markets in those gaps; they are not imputed.',
 'Primary treatment changes Coinbase to coherent CF Benchmarks DOGEUSD_RTI minute closes.',
 'No bar carrying, interpolation, z tuning, price-limit changes or outcome-based filtering.',
 'Each CF minute requires all sixty whole-second prices; then 61 consecutive closes are required.',
 '', 'FULL PERIOD AT FIVE CONTRACTS', 'Feed/scenario | Trades | Wins/losses | Net USD | Settled drawdown USD']
for feed,label in [('coinbase','Coinbase control'),('cf_close','CF close (feed-only)'),('cf_opening_average','CF + opening-average reference (separate change)')]:
    for scenario,desc in [('fees_only','fees only'),('plus1c','fees+1c/contract'),('plus2c','fees+2c/contract')]:
        lines.append(row(label+' / '+desc,m(feed,scenario)))
lines += ['', 'FEED-ONLY DIFFERENCE VERSUS COINBASE, FEES+1c/CONTRACT']
d=DEC['plus1c']
lines += [f"Net change: {d['extra_trades']:+} modeled trades, ${d['extra_net']:+.4f}.",
    f"Added markets: {d['added']['trades']} ({d['added']['wins']}W/{d['added']['losses']}L), net ${d['added']['net']:+.4f}.",
    f"Dropped markets: {d['dropped']['trades']}, old net ${d['dropped']['net']:+.4f}.",
    f"Overlap: {d['overlap']} markets; changed entry/lane/timing net difference ${d['overlap_net_difference']:+.4f}.",
    f"Frequency: Coinbase {m('coinbase')['trades_per_calendar_day']:.3f}/calendar day; CF {m('cf_close')['trades_per_calendar_day']:.3f}.",
    'These are standalone DOGE opportunities, not guaranteed extra fills alongside production.',
    'DOGE is not currently a traded live asset. Portfolio crowding from existing lanes was not replayed.',
    '', 'SOURCE COVERAGE AND MECHANISM',json.dumps(IC,sort_keys=True),json.dumps(COV,sort_keys=True),
    'Source validation: '+json.dumps(SI,sort_keys=True),
    'Precise official strikes are read from custom_strike.floor_strike; top-level floor_strike is truncated.',
    row('CF restricted to Coinbase-complete decision minutes',m('cf_common_coverage')),
    'The common-coverage diagnostic separates recovered observation time from changed prices/volatility.',
    'It still recomputes first entries, so selection can change across the whole market.',
    'New entries at previously incomplete Coinbase minutes: '+json.dumps(d['recovered_at_missing_source_minute']),
    'New entries at Coinbase-complete minutes: '+json.dumps(d['new_at_common_source_minute']),
    '', 'EARLY/LATEST PERIODS; EACH RESTARTS ITS HYPOTHETICAL CASH']
for period,desc in [('prior','Oct1–Oct9 15UTC'),('extension','Oct9 15UTC–Oct12 04:30UTC')]:
    for feed in ('coinbase','cf_close','cf_opening_average'):lines.append(row(desc+' / '+feed,m(feed,period=period)))
lines += ['', 'LANES — PRIMARY FEES+1c/CONTRACT']
for feed in ('coinbase','cf_close','cf_opening_average'):
    for lane,metrics in m(feed)['lanes'].items():lines.append(row(feed+' / '+lane,metrics))
lines += ['', 'EXECUTION SENSITIVITY']
for feed in ('coinbase','cf_close','cf_opening_average'):
    for scenario in ('cap_plus1c','delay60_plus1c','allcap_plus1c','allcap_plus2c'):lines.append(row(feed+' / '+scenario,m(feed,scenario)))
lines += ['All-cap assumes every qualifying market can fill at its allowed cap, even when the next quote fails.',
    'This exposes possible fill-selection bias; it is not an executable promise or a confidence bound.',
    'Delay60 uses a later quote outside the current 35-second freshness gate; diagnostic only.',
    '', 'DAILY NET — PRIMARY CF, FEES+1c/CONTRACT (ET SETTLEMENT DATE)',json.dumps(m('cf_close')['daily'],sort_keys=True),
    '', 'UNCERTAINTY AND INDIVIDUAL LOSSES',json.dumps(stats,sort_keys=True),
    '', 'EXACT RULES AND COSTS',
    'Original z>=2, favorite ask85–<95c, maximum buy93.9c; premium z>=3, ask93–<98c, cap97.5c.',
    'Both spread<=2c and 3–10minutes remaining; limit=min(signal ask+1c,lane cap), market tick ladder.',
    'Spot/reference are completed-minute close and exact pre-open minute close; 60 log returns population std.',
    'z=side*(spot/reference−1)/(sigma*sqrt(minutes remaining)). No price forecast or fitted threshold.',
    'The secondary opening-average variant changes ONLY reference to the sixty-second pre-open CF average.',
    'Five contracts, first qualifying lane, one market owner, one active DOGE position, hold to settlement.',
    'Standalone hypothetical $100; shared25contracts/$25reserved/$20daily including open risk; premium25/$25/$25 subordinate.',
    'Quadratic taker fee .07*multiplier*quantity*p*(1-p), rounded up to6decimals, debit up to4decimals.',
    'Primary fill is next-minute opening ask within cap/floor, assumed T+3s; no first-arrival or five-contract depth proof.',
    'The 1c/2c friction is an additional per-contract cost, not permission to exceed a buy cap.',
    'Recorded outcomes and settlement timestamps are retained; one trade equals one five-contract market entry.',
    '', 'VALIDATION',json.dumps(VALID,sort_keys=True)]
exploratory=P/'exploratory_lanes.json'
if exploratory.exists():
    lanes=json.loads(exploratory.read_text())
    lines += ['', 'EXPLORATORY LANE ISOLATION — POST-RESULT FOLLOW-UP',
        'Full signals replayed with only one lane permitted; not filtering the combined filled ledger.',
        'Results below are alternative strategies, not additive lanes. Same z/time/bands/caps/quantity.',
        'These comparisons were added after identifying the premium lane loss; do not call them held-out evidence.']
    for feed,fs in lanes['results'].items():
        for lane,cases in fs.items():
            for label,x in cases.items():lines.append(row(feed+' / '+lane+' / '+label,x))
    lines += ['CF-close z2-only: 20 modeled entries/20 wins; 1.788 per calendar day; fees + 1c net $5.8832, 2c $4.8832.',
        'All qualifying markets assumed filled at cap: 35 entries/34 wins/1 loss; net $3.7436 at 1c, $1.9936 at 2c.',
        'One loss omitted by the primary next-opening-quote proxy is included in this all-cap check.',
        'Recommendation is isolated prospective PAPER validation of CF-close z2-only, not live deployment.',
        'Do not select the opening-average reference merely because its in-sample total is better.']
lines += ['', 'BOUNDED LIVE MARKET-DATA CHECK']
probe=P/'feed_probe_summary.json'
if probe.exists():lines += [probe.read_text().strip()]
else:lines += ['Probe artifact not yet complete.']
lines += ['This short probe verifies access/arrival only. It does not prove long-run availability or fills.',
    'History endpoint can delay newest data by15minutes; a production version would need a live streaming collector.',
    '', 'ARTIFACTS',
    'PLAN.txt fixed before CF replay results. data/ preserves raw compressed index hours and frozen Coinbase/market inputs.',
    'analyze.py recreates every signal and trade; ledgers.json.gz stores all scenarios and periods.',
    'decomposition.json lists added/dropped trades; results.json has daily/lane/period statistics.',
    'preservation_before.json / preservation_final.json and manifest.json audit isolation and file hashes.',
    '', 'PRIMARY SOURCE DOCUMENTATION',
    'https://docs.kalshi.com/cfbenchmarks/rest-passthrough',
    'https://docs.cfbenchmarks.com/api/rest/historical-values/',
    'https://www.cfbenchmarks.com/data/indices/DOGEUSD_RTI',
    'https://docs.kalshi.com/websockets/cfbenchmarks-value']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:30]))
