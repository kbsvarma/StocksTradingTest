"""Offline audit of read-only production exports. Never instantiates an exchange client."""
import collections
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import sys
from decimal import Decimal as D
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parent
PACKAGE = P.parent.parent
data = json.loads((P / 'evidence.json').read_text())
cut, end = data['cutover_ts'], data['captured_ts']
orders = data['orders']
ET = ZoneInfo('America/New_York')

def stamp(t):
    return dt.datetime.fromtimestamp(t, ET).isoformat()

def quant(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return {'n': 0}
    return dict(n=len(v), median=statistics.median(v),
                p95=v[max(0, math.ceil(.95*len(v))-1)], minimum=v[0], maximum=v[-1])

def cohort(begin, finish):
    return [r for r in orders if begin <= r.get('created_ts', 0) < finish
            and r['asset'] not in ('COPPER', 'USDCAD')]

def delay(s, first, last):
    return (s[last]-s[first])*1000 if first in s and last in s else None

def metrics(rows):
    filled = [r for r in rows if D(r.get('filled', '0')) > 0]
    settled = [r for r in filled if r['status'] == 'settled']
    timing = collections.defaultdict(list)
    for r in rows:
        s = r['signal']
        for k in ('execution_book_gap_ms', 'execution_account_ms'):
            timing[k].append(s.get(k))
        for name, first, last in [
            ('qualified_to_submit_ms', 'signal_qualified_ts', 'execution_submit_ts'),
            ('execution_start_to_submit_ms', 'execution_started_ts', 'execution_submit_ts'),
            ('scan_start_to_submit_ms', 'scan_started_ts', 'execution_submit_ts'),
            ('submit_to_ack_ms', 'execution_submit_ts', 'execution_ack_ts')]:
            timing[name].append(delay(s, first, last))
        if s.get('execution_check_number') == 1:
            value = delay(s, 'signal_qualified_ts', 'execution_submit_ts')
            timing['first_check_qualified_to_submit_ms'].append(value)
            if r['asset'] == 'WTI' and r['lane'] == 'cushion_z2':
                timing['wti_original_first_check_qualified_to_submit_ms'].append(value)
        if r.get('fill_confirmed_ts') and s.get('execution_submit_ts'):
            timing['submit_to_local_confirm_ms'].append(
                1000*(r['fill_confirmed_ts']-s['execution_submit_ts']))
    wti = [r for r in filled if r['asset'] == 'WTI' and r['lane'] == 'cushion_z2']
    mean = lambda v: statistics.mean(v) if v else None
    return dict(markets=len(rows), filled_markets=len(filled),
        settled=len(settled), open=len(filled)-len(settled),
        wins=sum(r['result']==r['side'] for r in settled),
        losses=sum(r['result']!=r['side'] for r in settled),
        net_after_fees=str(sum((D(r['pnl']) for r in settled), D(0))),
        timings={k: quant(v) for k,v in timing.items()},
        wti_original=dict(n=len(wti), mean_entry_cents=mean([float(r['entry'])*100 for r in wti]),
            mean_profit_if_wins_after_fees=mean([float(D(r['filled'])-D(r['cost'])-D(r['fees'])) for r in wti]),
            mean_fill_minus_signal_quote_cents=mean([(float(r['entry'])-r['signal']['signal_ask'])*100 for r in wti]),
            mean_fill_minus_scan_depth_vwap_cents=mean([(float(r['entry'])-r['signal']['entry'])*100 for r in wti])))

pre, post = cohort(cut-21600, cut), cohort(cut, end)
report = dict(cutover=stamp(cut), captured=stamp(end), elapsed_hours=(end-cut)/3600,
              before_6h=metrics(pre), after=metrics(post), before_24h=metrics(cohort(cut-86400, cut)))

receipts = json.loads((P/'exchange_receipts.json').read_text())
prior = json.loads((P/'prior_exchange_receipts.json').read_text())
by_ticker = {r['ticker']: r for r in orders}
for name, source in [('before_6h', prior), ('after', receipts)]:
    measured, confirmation = [], []
    for row in source['rows']:
        r = by_ticker[row['ticker']]
        last_fill = max(dt.datetime.strptime(f['created_time'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=dt.timezone.utc).timestamp()
                        for f in row['fills'])
        measured.append((last_fill-r['signal']['execution_submit_ts'])*1000)
        confirmation.append((r['fill_confirmed_ts']-last_fill)*1000)
    report[name]['timings']['submit_to_exchange_last_fill_ms'] = quant(measured)
    report[name]['timings']['exchange_last_fill_to_local_confirm_ms'] = quant(confirmation)

post_events = [r for r in data['events'] if r['_event_ts'] >= cut]
qualified = [r for r in post_events if r.get('signal_qualified')]
unique = sorted({r['ticker'] for r in qualified})
report['opportunities'] = dict(
    event_rows=len(post_events), qualified_rows=len(qualified), qualified_markets=len(unique),
    qualified_markets_without_fill=[t for t in unique if D(by_ticker.get(t, {}).get('filled', '0')) == 0],
    status_rows=dict(collections.Counter(r.get('status') for r in post_events)),
    qualified_book_transports=dict(collections.Counter(r.get('book_transport', {}).get('source') for r in qualified)),
    per_asset={a: dict(collections.Counter(r.get('status') for r in post_events if r['asset']==a))
               for a in sorted({r['asset'] for r in post_events})},
    incomplete_underlying_decisions=[dict(asset=a, decision=stamp(t), checks=n)
        for (a,t),n in collections.Counter((r['asset'],r['decision_ts']) for r in post_events
                                           if r.get('status')=='incomplete_spot').items()])

report['trades'] = []
for r in post:
    s=r['signal']
    report['trades'].append(dict(ticker=r['ticker'], asset=r['asset'], lane=r['lane'],
        status=r['status'], side=r['side'], count=r['filled'], price_cents=float(r['entry'])*100,
        fees=r['fees'], net_after_fees=r.get('pnl'), signal_quote_cents=s['signal_ask']*100,
        executable_depth_vwap_cents=s['entry']*100,
        fill_vs_cap_gross_saving=str((D(r['favorite_limit'])-D(r['entry']))*D(r['filled'])),
        fill_transport=r.get('fill_transport'), checks=s['execution_check_number'],
        submit_to_ack_ms=delay(s, 'execution_submit_ts', 'execution_ack_ts'),
        execution_start_to_submit_ms=delay(s, 'execution_started_ts', 'execution_submit_ts'),
        qualified_to_submit_ms=delay(s, 'signal_qualified_ts', 'execution_submit_ts')))

# Reproduce the legacy-action guard against actual REST receipt fields without
# rewriting action='sell' to action='buy' as the original economics replay did.
# This tests protocol compatibility; these are NOT captured WebSocket frames.
sys.path.insert(0, str(PACKAGE/'candidate'))
from stream_transport import FillState
replays=[]
for row in receipts['rows']:
    record=by_ticker[row['ticker']]
    results={}
    for mode in ('actual_legacy_action', 'diagnostic_force_buy'):
        cache=FillState()
        for receipt in row['fills']:
            f=dict(receipt)
            if mode=='diagnostic_force_buy':
                f['action']='buy'
            cache.apply(f, receipts['ts'])
        results[mode]=cache.confirmed(row['order'],record) is not None
    replays.append(dict(ticker=row['ticker'], actions=sorted({f['action'] for f in row['fills']}), **results))
assert all(not x['actual_legacy_action'] and x['diagnostic_force_buy'] for x in replays)
report['fill_guard_replay'] = dict(mode='REST_RECEIPT_FIELDS_NOT_CAPTURED_WS_FRAMES', results=replays,
    limitation='Production does not persist raw fill frames or per-rejection reasons. The code incompatibility is reproduced; exact historical WS payloads cannot be recovered from this ledger.')

deployment=json.loads((PACKAGE/'receipts/deployment_receipt.json').read_text())
report['source_changed_since_deployment']=[n for n,h in deployment['source_hashes'].items()
    if n.endswith('.py') and data['source_hashes'].get(n)!=h]
assert not report['source_changed_since_deployment']
report['health']=data['status']
report['clock_ntp']=data['ntp']
report['limits']=dict(contracts=data['status']['contracts'], max_contracts=data['status']['max_contracts'],
                     max_risk=data['status']['max_risk'], daily_loss_budget=data['status']['daily_loss_budget'])
report['limitations']=[
    'Only four post-cutover trades; there is no randomized simultaneous REST control.',
    'First-check latency excludes retries deliberately waiting for a book within the unchanged cap.',
    'All-signal qualified-to-submit median rises because two of four post-cutover signals wait for price, not because the submission path is slower.',
    'Fee-net WTI winning payout comparison controls asset/lane/quantity but compares different markets and outcomes; it is not causal.',
    'Kalshi scheduled Thursday 03:00-05:00 ET maintenance reduces the tradable post-cutover period.',
    'Two early underlying source decisions after reopening were not assessable plus one WTI decision; absence of z evidence is not a known missed qualified trade.',
    'Exact reconnection downtime and fill-notification receipt times are not persisted by production.',
]
(P/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['cutover','captured','before_6h','after','trades','fill_guard_replay','limits']},indent=2))
