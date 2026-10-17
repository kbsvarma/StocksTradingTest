"""Independent receipt validation and readable findings."""
import collections as C,datetime as dt,gzip,hashlib,json,sys
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'production_source'))
from lanes import lane_for,Z2
def read(n):return json.loads((P/n).read_text())
def gz(n):
 with gzip.open(P/n,'rt') as f:return json.load(f)
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
R=read('receipt_results.json');rows=read('receipt_rows.json');orders=gz('orders.json.gz');events=gz('events.json.gz');ET=ZoneInfo('America/New_York')
def et(t):return dt.datetime.fromtimestamp(t,ET).isoformat()
def independent_book(levels,cap,floor):
 need=D(5);cost=D(0);below=False
 for px,n in sorted((1-D(px),D(n)) for px,n in levels):
  if n<=0:continue
  if px<D(str(floor)):below=True
  if px>D(str(cap)):break
  qty=min(need,n);cost+=qty*px;need-=qty
  if not need:break
 return need==0 and not below,cost/5 if need==0 else None
def model_fee(p):
 cost=5*p;raw=(D('.07')*cost*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 return (cost+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-cost
checks=C.Counter();cases=[]
for o in orders:
 assert D(o['pnl'])==D(o['filled'])*int(o['result']==o['side'])-D(o['cost'])-D(o['fees'])
 assert D(o['filled'])==5
 ok,px=independent_book(o['signal']['execution_book_levels'],o['favorite_limit'],lane_for(o).floor)
 assert ok
 checks['actual_ledger_and_final_depth']+=1
for r in rows:
 b=r['first_supported']
 if not b:continue
 f=b['signal'];e=next(e for e in events if e['ticker']==r['ticker'] and e.get('book_observed_ts')==b['book_ts'])
 ok,px=independent_book(e['book_levels'],b['cap'],lane_for(f).floor)
 ok2,px2=independent_book(e['execution_book_levels'],b['cap'],lane_for(f).floor)
 assert ok and not ok2 and e['execution_book_ts']>b['book_ts'] and e['execution_status']=='Insufficient depth inside cap'
 assert f['decision_ts']<r['decision_ts'] and b['book_ts']<f['decision_ts']+25 and r['quantity']==5
 assert abs(float(px)-b['average'])<1e-8
 net=5*(D(int(r['won']))-px)-model_fee(px);delta=net-D(str(r['net']))
 assert abs(float(delta)-r['earliest_supported_counterfactual']['net_delta'])<1e-8
 checks['earlier_depth_and_fee_cases']+=1
 current=f['z']>=lane_for(f).threshold(f['remaining_minutes'])
 cases.append(dict(ticker=r['ticker'],asset=r['asset'],side=r['side'],earlier_signal_time=et(f['decision_ts']),actual_signal_time=et(r['decision_ts']),book_received=et(b['book_ts']),actual_entry=r['entry'],earlier_entry=float(px),cap=b['cap'],quantity=5,minutes_earlier=r['earliest_supported_counterfactual']['minutes_earlier'],net_saving_if_captured=float(delta),earlier_z=f['z'],historical_z_required=f.get('required_z'),current_z_required=lane_for(f).threshold(f['remaining_minutes']),passes_current_z=current,vanished_by_next_check_ms=1000*(e['execution_book_ts']-b['book_ts']),next_book_best_ask=min(1-float(p) for p,q in e['execution_book_levels'] if float(q)>0),old_execution_status=e['execution_status'],earlier_book=e['book_levels'],next_book=e['execution_book_levels']))
coverage=read('receipt_coverage.json');assert coverage['observed_lane_minutes']==coverage['expected_lane_minutes']==3520
assert len(rows)==len({r['ticker'] for r in rows})==233
assert sum(r['won'] for r in rows)==222
before=read('preservation_before.json');after=read('preservation_final.json')
assert all(before[k]==after[k] for k in ('hashes','flags','services'))
save('validation.json',dict(passed=True,checks=dict(checks),coverage=coverage,production_unchanged=True,input_hashes={n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ('orders.json.gz','attempts.json.gz','events.json.gz','observer.json.gz')}))
save('earlier_book_cases.json',cases)
lines=[]
def add(s=''):lines.append(s)
add('Earlier-entry price review — October 14, 2026')
add('Read-only. Live strategy, prices, size, risk limits, activation flags and services unchanged. WTI/EURUSD lower-z research remains parked.')
add('Coverage: actual filled, settled entries since October 8 00:00 ET through capture '+et(read('capture_receipt.json')['captured_ts'])+'. Seven active assets had fills; USD/CAD had none in this snapshot. All 233 trades were five contracts. Retired Copper excluded.')
add()
add('Finding')
add('Yes, earlier entries sometimes offered materially better prices on individual trades. Most earlier cheap prices did not yet qualify. Three brief, recorded five-contract opportunities passed the rules then, but disappeared by the final book check 204–256 milliseconds later. They imply only $0.5746 conditional savings after modeled fees if all had actually been captured. Two WTI cases still pass current rules and account for $0.3853. The third is an old BTC z2 case that fails the now-required z3. These are timing candidates, not proved missed fills or an expected recurring profit.')
add()
add('Actual result for this sample')
add('233 trades: 222 wins, 11 losses; actual net after charged fees +$25.4967. Winning-trade net +$75.4185; losing-trade net -$49.9218. This is the captured local reconciled ledger, not a new exchange account audit.')
add('All 3,520 expected pre-entry lane-minute scans are present. There is no missing scan interval used to infer a first signal. One earlier WTI minute has a recorded incomplete source, which stays unknown rather than being backfilled from later data.')
add()
add('Of 222 winners:')
add('- 193 had at least one earlier recorded cheaper quote on the same eventual side.')
add('- 120 had an earlier cheaper quote inside their lane price band, spread and time window.')
add('- Of those 120, 98 had only below-threshold z at the cheaper checks; one had incomplete source data; 21 had at least one cheaper qualified quote.')
add('- Looking at all earlier qualified signals, regardless of whether their quote was cheaper, 24 winning markets qualified in an earlier minute. The other 198 entered at their first recorded qualifying minute.')
add('- Of those 24, three had a recorded earlier five-contract book within the cap; all three were preliminary books, and their final books no longer fit. The other 21 never showed qualifying depth in the available samples. Unsampled liquidity is unknown.')
add('- None of the 11 losing trades had an earlier recorded qualified minute. Nine had earlier cheaper quotes, two inside band/spread/time but below z. This does not imply lowering z is safe: the cohort is conditional on eventual live entry.')
add()
add('By asset: winners / earlier cheaper in allowed band+spread+time / earlier cheaper qualified / earlier full-depth supported')
for asset in sorted({x['asset'] for x in rows}):
 rr=[x for x in rows if x['asset']==asset and x['won']]
 add(f"{asset}: {len(rr)} / {sum(x['earlier_cheaper_in_band']>0 for x in rr)} / {sum(x['within_band_gate_counts'].get('qualified',0)>0 for x in rr)} / {sum(x['first_supported'] is not None for x in rr)}")
add()
add('Concrete earlier books')
for c in cases:
 add(f"{c['ticker']} {c['side'].upper()}: actual average {100*c['actual_entry']:.3f}c; earlier five-contract book {100*c['earlier_entry']:.3f}c; {c['minutes_earlier']:.0f} minute(s) earlier. Modeled saving after fees ${c['net_saving_if_captured']:.4f} for five contracts. Earlier signal {c['earlier_signal_time']}; actual entry signal {c['actual_signal_time']}.")
 add(f"Earlier cap {100*c['cap']:.3f}c. At the final check {c['vanished_by_next_check_ms']:.1f}ms later, best available ask was {100*c['next_book_best_ask']:.3f}c; order correctly blocked by the price/depth rule. Earlier z {c['earlier_z']:.4f}, historical threshold {c['historical_z_required']}, current threshold {c['current_z_required']}; passes current z={c['passes_current_z']}.")
add('These elapsed times bound disappearance by the next observation; they do not prove the cheap quote remained available for the full interval. A faster order might still miss or partially fill. Full account/risk approval is not replaced by preliminary depth. No proposal to remove the final validation or raise the limit is implied.')
add('Illustrative matched-market savings: all three $0.5746; two still passing today’s rules $0.3853. Charging an additional 1c per contract haircut to the hypothetical earlier fills would reduce those figures to $0.4246 and $0.2853 respectively. These sums are not a full-strategy replay: changing entry timing can also create new trades outside this selected cohort, alter capital availability, and change subsequent outcomes.')
add()
add('Are we generally paying more because execution is slow?')
add('Across all 233 entries, actual fill averaged 0.2792c per contract BELOW the completed-minute signal ask. Compared with the final observed five-contract book, 198 fills matched, 20 improved and 15 worsened. Gross unfavorable movement versus final book totaled $0.12594; favorable movement totaled $0.63970. This is descriptive, not a tradable benchmark or a savings estimate. The order cap is not automatically the fill price; matching can execute below it.')
add('The three brief earlier books above predate the October 14 timing release. Latest two filled trades after that release were WTI expiries 16:15 and 16:30 ET; both won, net +$0.6779, and both entered on the first qualifying minute. One had a cheaper earlier within-band quote, but it failed z. Their sample is too small to estimate a new miss rate.')
for x in rows:
 if x['created']<1792002480:continue
 z=x['latency'];add(f"{x['ticker']}: actual {100*x['entry']:.1f}c, z{x['z']:.4f} at {x['rem']:.0f} minutes; qualified-to-POST {1000*z['qualified_to_submit']:.1f}ms; decision-boundary-to-POST {z['decision_to_submit']:.3f}s; POST-to-ack {1000*z['submit_to_ack']:.1f}ms; final-book-to-POST {1000*z['finalbook_to_submit']:.1f}ms.")
add('Older records lack submit/qualification telemetry. Their observed-signal-to-intent or decision-to-intent times are not labeled as POST latency. Larger intervals often include intentional retries waiting for eligible depth, not CPU delay.')
add()
add('Practical conclusion')
add('The measured opportunity is reacting to fleeting cheaper books as soon as the existing signal qualifies. Recent timing changes already shorten that path, and the latest two fills show 172–177ms from qualification to submission. This study does not justify weakening z or entering before qualification to capture the much larger set of hindsight-cheap prices. Keep live parameters unchanged and evaluate newly collected executions separately; do not count an old BTC z2 opportunity as eligible under current z3.')
add('If a future earlier-entry rule is proposed, freeze it before testing and replay all markets including those the live bot never entered. A winners-only analysis cannot establish its expectancy. This request’s analysis is a contemporaneous execution diagnostic, not a new strategy backtest or an activation.')
add()
add('Evidence and checks')
add('Read-only database transaction: 233 orders, 238 attempt records, 7,737 matching events; 2,120 independent observer records, no incomplete observer lines. Preserved actual failed checks, zero-fill attempts, losing trades and incomplete source records. No API queries, orders or live writes used. Original captured receipts retained.')
add('Independent Decimal checks reproduce actual P&L for all 233 trades, full final-book depth for all entries, and all three earlier-book cost/fee calculations. The three next-check books independently fail the cap. All pre-entry lane-minute slots accounted for. Source/config fingerprints, activation flags and all four service PIDs unchanged.')
add('Files: PLAN.txt, capture_receipt.json, receipt_results.json, receipt_rows.json, earlier_book_cases.json, receipt_coverage.json, validation.json, orders.json.gz, attempts.json.gz, events.json.gz, observer.json.gz; receipt_analysis.py and finalize.py reproduce the analysis/report.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print('VALIDATED',dict(checks),'report',P/'REPORT.txt')
