"""Save this review's findings and notification state; never mutate the ledger."""
import hashlib
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LIVE = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
r = json.loads((HERE / 'report.json').read_text())
ops = json.loads((HERE / 'operations.json').read_text())
checks = json.loads((HERE / 'review_checks.json').read_text())
orders = json.loads((HERE / 'orders.json').read_text())
state_path = ROOT / 'review_state.json'
state = json.loads(state_path.read_text())
assert state['last_review_ts'] < r['ts'], 'Review already finalized'
now = time.time()
status = json.loads((ROOT / 'status.json').read_text())
assert now - status['ts'] < 30 and status['health'] == ''
assert status['mode'] == 'PAPER_ONLY' and status['clock']['synchronized']
assert status['config_hash'] == '5cb04cf3ce744089433117d9b210666a1bf9095d9805f83d73286f5de4e289ac'
current_hashes = {n: hashlib.sha256((LIVE / n).read_bytes()).hexdigest()
                  for n in ops['production_source_hashes']}
assert current_hashes == ops['production_source_hashes']
flags = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
         for f in (LIVE / 'state').iterdir()
         if f.is_file() and f.name.startswith(('ARM', 'HALT'))}
assert flags == ops['production_flags']
services = subprocess.check_output(['systemctl', '--user', 'show',
    'kalshi-crypto-z2-live.service', 'kalshi-dashboard.service',
    'kalshi-execution-observer.service', 'kalshi-tennis90-paper.service',
    'kalshi-resting-paper.service', '-p', 'Id', '-p', 'MainPID',
    '-p', 'ActiveState', '-p', 'NRestarts'], text=True)
assert services == ops['production_services']
ntp = subprocess.check_output(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], text=True).strip()
assert ntp == 'yes'
(HERE / 'final_preservation.json').write_text(json.dumps({
    'ts': now, 'status': status, 'heartbeat_age_seconds': now - status['ts'],
    'ntp': ntp, 'source_hashes_unchanged': True, 'flags_unchanged': True,
    'service_pids_unchanged': True, 'services': services,
    'collector_or_live_changes': False,
}, indent=2) + '\n')

from decimal import Decimal as D
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
cohort = checks['common_complete_cohort']
variants = ('rest_3c_10s', 'rest_1c_10s', 'paper_ioc')
new = [o for o in orders if o['created_ts'] > state['last_review_ts']]
new_cancels = [{k:o.get(k) for k in ('ticker','variant','created_ts','filled','coverage_complete','end_reason')}
              for o in new if o.get('end_reason','').startswith('health_cancel:')]
new_fills = [{k:o.get(k) for k in ('ticker','variant','price','filled','pnl','coverage_complete','end_reason')}
             for o in new if o['variant']!='paper_ioc' and D(o['filled'])>0]
(HERE/'review_state.before.json').write_text(json.dumps(state,indent=2)+'\n')
(HERE/'new_evidence.json').write_text(json.dumps(dict(fills=new_fills,health_cancellations=new_cancels),indent=2)+'\n')
lines = ['RESTING PAPER REVIEW — '+datetime.fromtimestamp(r['ts'],timezone.utc).isoformat(),
 'Simulated execution only; frozen parameters and all existing bots unchanged.',
 'Routine collection continues; no new failure or notification milestone.',
 '', 'COMMON COMPLETE SETTLED COHORT: '+str(cohort['n'])+' markets',
 'Actual live: '+json.dumps(cohort['actual']),
 'Variant | fills | wins/losses | net after modeled fees | +1c friction | taker stress | drawdown']
for v,x in cohort['variants'].items():
 lines.append(f"{v} | {x['filled_markets']} | {x['wins']}/{x['losses']} | {x['net_after_fees']} | {x['net_after_1c_friction']} | {x['net_taker_fee_stress']} | {x['settled_drawdown']}")
lines += ['', 'The same 63 markets are used above for all variants and actual live.',
 'Individual complete cohorts: 3c=63, 1c=64, IOC=72. NATGAS 01:15 was a',
 'post-only skip for 1c but later health-canceled for 3c/IOC; therefore it is',
 'excluded from the common cohort. No missing window is called a proven miss.',
 '', 'ALL RECORDS (different coverage; do not compare raw totals as paired returns)']
for v,x in r['variants'].items():
 lines.append(v+': '+json.dumps({k:x[k] for k in ('opportunities','attempted','filled_markets','settled','wins','losses','partial_fills','net_after_fees','net_after_1c_friction','net_taker_fee_stress','unknown_coverage')}))
lines += ['Actual live all records: '+json.dumps(r['actual_live']),
 'The 1c all-recorded net includes a historical +$0.60 fill in interrupted',
 'coverage. That winning record remains preserved but outside complete cohorts.',
 '', 'MATCHED PRICE IMPROVEMENT AND MISSED OPPORTUNITIES']
for v in variants:
 c=r['comparisons'][v]
 lines.append(v+': '+json.dumps({k:c[k] for k in ('matched_same_side','weighted_entry_improvement_cents','missed_live_winners','missed_live_winner_pnl','avoided_live_losses_complete','paper_only_fills','live_filled_paper_unfilled_unknown','filled_per_elapsed_24h','calendar_days')}))
lines += ['', 'ASSET / LANE COUNTS AND NET (all records)']
for key in ('by_asset','by_lane'):
 for v,groups in r[key].items():
  for name,x in groups.items():
   lines.append(f"{key} {v} {name}: opportunities={x['opportunities']}, fills={x['filled_markets']}, W/L={x['wins']}/{x['losses']}, net={x['net_after_fees']}")
lines += ['', 'NEW FILLS SINCE LAST REVIEW (all simulated)', json.dumps(new_fills,indent=2),
 '', 'OPERATIONS',
 f"{ops['receipt_hashes_verified']} raw receipt hashes verified; {ops['trade_ids']} unique public trades; no corrections, HTTP429s, receipt request errors, or truncated pages.",
 'NTP synchronized; service active, same PID, fresh heartbeat; no current health block.',
 'Eight status errors are preserved startup metadata-cache rejections; no new HTTP failures.',
 'Signal delivery seconds: '+json.dumps(ops['signal_delivery_seconds']),
 'Signal to paper acceptance seconds: '+json.dumps(ops['signal_to_accept_seconds']),
 'Print publication seconds: '+json.dumps(ops['public_print_delay_seconds']),
 f"Minimum request spacing {checks['http_spacing_min']:.6f}s; max {checks['max_requests_per_second']}/s.",
 f"Largest lifetime book gap {max(g['max_gap_including_tail'] for g in checks['book_gaps_including_tail']):.6f}s, below4s limit.",
 'Receipt audit replays visible queues, print direction/time/size, fees, full five-contract IOC depth, caps, official settlements and cancellation races.',
 'Quote touches alone never fill. All filled orders currently total five contracts; some aggregate fractional prints. No terminal partial fills.',
 'Eleven 3c / ten 1c health cancellations cumulatively, plus six historical flag blocks.',
 'Two new affected market windows remain inconclusive; no new journal errors. The exact live status field causing the transient health cancellation was not retained; do not infer it.',
 'A below-bid print may credit quantity under the frozen model even while displayed queue remains. Hidden queue and counterfactual market responses are not known.',
 'The follower receives production signals after live publication, sometimes after submission; the one-shot paper IOC is not a replay of live retries.',
 '', 'UNCERTAINTY AND DECISION',checks['uncertainty'],
 'Only 1.506 elapsed days and three partial ET calendar dates. Positive sample net is not proof of an edge. The first-20 and first-loss reports already occurred.',
 'Continue frozen collection; no new material finding warrants a repeat notification. No repair, strategy change, financial request or service restart.',
 'Report-only review_checks.py now compares the intersection of fully observed markets, preserving separate variant-specific cohorts; earlier report files remain untouched.',
 'All production hashes, flags and five service PIDs unchanged. State update deduplicates notifications; ledger remains read-only.',
 'Entry cutoff remains Oct28 17:11 ET; no automatic live promotion.']
(HERE/'FINDINGS.txt').write_text('\n'.join(lines)+'\n')
state.update(last_review_ts=r['ts'],last_review_path=str(HERE),
 last_counts={v:{k:x[k] for k in ('opportunities','filled_markets','settled','net_after_fees')} for v,x in r['variants'].items()},
 last_comparable_settled={v:c['comparable_settled_windows'] for v,c in r['comparisons'].items()},
 last_common_comparable_settled=cohort['n'],last_common_cohort_summary={k:v for k,v in cohort.items() if k!='tickers'},
 last_health=status['health'],last_health_verified_ts=now,
 last_observed_health_cancels=11,
 last_observed_health_cancels_by_variant={v:r['variants'][v]['end_reasons'].get('health_cancel:live_not_healthy',0) for v in variants},
 last_review_decision='DONT_NOTIFY: healthy collection; no new material failure, sample milestone or actionable edge finding.',
 last_review_note='Four additional 1c winners and one 3c winner; no additional resting loss. Same rules and prior conclusions; await evidence horizon.')
# Preserve all previously reported milestone and material-notification fields.
assert state['first_resting_loss_reported'] and state['preliminary_20_reported']
tmp=state_path.with_suffix('.json.tmp')
tmp.write_text(json.dumps(state,indent=2)+'\n'); tmp.replace(state_path)
(HERE/'review_state.json').write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(dict(saved=str(HERE),decision='DONT_NOTIFY',common_complete=cohort['n'],production_preserved=True)))
