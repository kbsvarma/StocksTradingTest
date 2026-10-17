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
assert not state.get('first_resting_loss_reported'), 'Already reported; do not duplicate'
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

loss_market = 'KXEURUSD15M-26OCT152315-30'
losses = [o for o in orders if o['ticker'] == loss_market and o['variant'].startswith('rest_')]
assert len(losses) == 2 and all(o['settled'] and o['coverage_complete'] and o['result'] == 'yes' for o in losses)
new_cancels = [dict(ticker=o['ticker'], variant=o['variant'], created_ts=o['created_ts'],
                    end_reason=o['end_reason']) for o in orders
               if o['created_ts'] > state['last_review_ts'] and o.get('end_reason', '').startswith('health_cancel:')]
(HERE / 'new_health_cancellations.json').write_text(json.dumps(new_cancels, indent=2) + '\n')

findings = '''RESTING PAPER REVIEW — 2026-10-16 03:19 UTC / Oct 15 23:19 ET

Substantive new result: first losing resting fill, EUR/USD
KXEURUSD15M-26OCT152315-30. Both independent simulations bought five NO
contracts; official outcome YES. 1-cent discount entered at 89 cents and lost
$4.45; 3-cent discount entered at 87 cents and lost $4.35, after modeled fees.
The actual live trade entered five at 91 cents and lost $4.5787 including fees.
These cheaper simulated entries saved $0.1287 and $0.2287 on that trade.

The print supporting both fills occurred 37.932 milliseconds before the
10-second expiry and was first observed 12.143 seconds later. It arrived
within the frozen 15-second publication grace. It traded through the bid;
the frozen model permits that fill even with displayed queue remaining.
This is counterfactual execution evidence, not an exchange-confirmed order.
Original receipts and queue values are retained. No rules were changed.

SAME 51 FULLY OBSERVED, SETTLED COMPARABLE MARKET OPPORTUNITIES
Design             Fills  Wins/Losses  Net after fees  Net with +1c friction
Actual live        40     37 / 3      -$0.3299        n/a
1c resting         12     11 / 1      +$0.0100        -$0.5900
3c resting          4      3 / 1      -$2.6000        -$2.8000
One-shot paper IOC 19     18 / 1      +$2.5671        +$1.6171

The one-shot paper IOC does not reproduce the live runner's retries or its
earlier access to signals. All paper fills are simulated. The two resting
variants are separate counterfactual portfolios, not additive positions.
Taker-fee stress for this comparable sample: 1c resting -$0.3094;
3c resting -$2.7476. Settled drawdown: $4.45 and $4.35 respectively.

ALL RECORDS (66 opportunities; a different denominator)
1c resting: 13 fills, 12 wins / 1 loss, +$0.61; +1c friction -$0.04.
3c resting: 4 fills, 3 wins / 1 loss, -$2.60; +1c friction -$2.80.
Paper IOC: 27 fills, 25 wins / 2 losses, +$0.6713.
Actual live: 54 fills, -$5.2953; this includes incomplete paper windows.
The $0.60 difference between 1c all-recorded and comparable results is a
winning fill in interrupted coverage. It is preserved in the ledger and
all-recorded results, but excluded from the fully observed paired sample.

PRICE AND OPPORTUNITY COST
1c resting matched 11 live entries with weighted entry improvement 1.173c;
missed 28 live winners worth $10.1722 in complete windows, and avoided two
live losses. Another 13 live-filled windows have inconclusive paper coverage.
3c resting matched four entries, improving price 2.75c; missed 34 live winners
worth $11.9191, and avoided two live losses; 14 live-filled windows inconclusive.
1c paper-only winning fills: WTI Oct14 17:30 and EUR/USD Oct15 22:15.
No terminal partial fills. Some full fills aggregate fractional prints.
Frequency, asset and lane splits are retained in report.json. Only roughly
1.26 elapsed days and two partial calendar days are available; this is too
little independent evidence for a durable edge or reliable uncertainty bound.

OPERATIONS
Service active, NTP synchronized, fresh heartbeat, no current health error.
1,348 hashed raw receipts verified; 2,936 unique public trade IDs; zero
corrections, HTTP 429s or incomplete pagination. Status retains eight old
startup/cache error counts, with no new request errors in this audit.
Live saved -> paper observed: median 48.4ms, maximum 101.2ms.
Signal -> paper acceptance: median 1.397s, maximum 1.816s.
Public print -> first arrival: median 1.597s, maximum 14.339s.
Largest book gap including order lifetime tails: 1.166s (limit 4s).
Minimum HTTP request spacing: 0.500018s; maximum two requests per second.
Fee metadata, original price caps, admissions, print direction/quantity,
cancel/expiry boundaries, settlement receipts and arithmetic passed audit.
Quote touches alone did not fill. There are 15 incomplete resting windows:
nine health cancellations plus six historical pause-guard blocks. Two new
health cancellations per variant occurred since the prior review; the exact
live status field causing them was not retained and is not inferred here.
These windows are inconclusive, not proven misses or avoided losses.

No collector repair or restart was necessary. Live source hashes, ARM/HALT
flags and five service PIDs were unchanged throughout the review. No orders
were submitted and no bot parameters changed. Continue the frozen test;
positive net alone is not evidence of an edge. The 20-comparable milestone
was previously reported; this notification is solely the new first-loss result.
Final entry cutoff remains Oct28 17:11 ET, then finish settlements and review.

Evidence: report.json, operations.json, review_checks.json, orders.json,
actual.json, final_preservation.json, new_health_cancellations.json.
'''
(HERE / 'FINDINGS.txt').write_text(findings)

(HERE / 'review_state.before.json').write_text(json.dumps(state, indent=2) + '\n')
notification = ('First simulated resting-order loss: EUR/USD lost $4.45 with the '
    '1-cent discount and $4.35 with the 3-cent discount. Across 51 fully observed '
    'opportunities, net after modeled fees is +$0.01 for 1-cent resting and '
    '-$2.60 for 3-cent resting, versus -$0.33 for the live bot on the same markets. '
    'Adding 1 cent per contract of friction puts the 1-cent version at -$0.59. '
    'Collector healthy; live bot unchanged. Results remain preliminary.')
complete_pairs = {v: sum(any(o['ticker'] == pair['ticker'] and o['variant'] == v
                             and o.get('coverage_complete') for o in orders)
                         for pair in r['comparisons'][v]['paired'])
                  for v in ('rest_1c_10s', 'rest_3c_10s')}
state.update(last_review_ts=r['ts'], last_review_path=str(HERE),
    last_counts={v:{k:s[k] for k in ('opportunities','filled_markets','settled','net_after_fees')}
                 for v,s in r['variants'].items()},
    last_comparable_settled={v:c['comparable_settled_windows'] for v,c in r['comparisons'].items()},
    last_health=status['health'], last_health_verified_ts=now,
    last_material_event='first_resting_loss_EURUSD_20261016T031919Z',
    last_material_report_ts=now, first_resting_loss_reported=True,
    first_resting_loss_market=loss_market, first_resting_loss_path=str(HERE),
    last_observed_health_cancels=9, last_reported_health_cancels=9,
    last_reported_complete_matched_pairs=complete_pairs['rest_1c_10s'],
    last_reported_complete_matched_pairs_by_variant=complete_pairs,
    last_review_decision='NOTIFY: first losing resting trade; comparable result nearly flat or negative.',
    notification_text=notification)
temp = state_path.with_suffix('.json.tmp')
temp.write_text(json.dumps(state, indent=2) + '\n')
temp.replace(state_path)
(HERE / 'review_state.json').write_text(json.dumps(state, indent=2) + '\n')
print(json.dumps({'saved':str(HERE), 'live_preserved':True, 'notification':notification}))
