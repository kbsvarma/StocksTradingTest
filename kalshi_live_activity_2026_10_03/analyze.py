"""Read-only analysis of saved production and authenticated exchange snapshots."""
import collections
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
snapshot = json.loads((ROOT / 'production_snapshot.json').read_text())
diagnostics = json.loads((ROOT / 'diagnostics.json').read_text())
activation = json.loads((ROOT.parent / 'kalshi_z3_live_2026_10_02/activation_receipt.json').read_text())
ET = ZoneInfo('America/New_York')
start, end = activation['verified_ts'], snapshot['snapshot_ts']
orders = snapshot['orders']

def stats(rows):
    settled = [r for r in rows if r['status'] == 'settled']
    return dict(attempts=len(rows), filled_markets=sum(float(r.get('filled', 0)) > 0 for r in rows),
                contracts=sum(float(r.get('filled', 0)) for r in rows),
                settled=len(settled), wins=sum(r['side'] == r['result'] for r in settled),
                losses=sum(r['side'] != r['result'] for r in settled),
                pnl=round(sum(float(r.get('pnl', 0)) for r in rows), 6))

post = [r for r in orders if start <= r['created_ts'] <= end]
exchange = {r['order']['order_id']: r['order'] for r in diagnostics['exchange_orders']}
confirmed = all(exchange[r['order_id']]['status'] == 'executed'
                and float(exchange[r['order_id']]['fill_count_fp']) == float(r['filled']) == 5
                and float(exchange[r['order_id']]['remaining_count_fp']) == 0 for r in post)
days = collections.defaultdict(list)
for r in orders:
    day = dt.datetime.fromtimestamp(r['created_ts'], ET).date().isoformat()
    if day >= '2026-09-23':
        days[day].append(r)
lanes = {}
for lane, threshold in [('cushion_z2', 2), ('premium_z3', 3)]:
    events = [e for e in diagnostics['events'] if e.get('lane') == lane]
    qualified = {e['ticker'] for e in events if e.get('z', 0) >= threshold}
    filled = {r['ticker'] for r in post if r.get('lane') == lane}
    lanes[lane] = dict(minute_scans=len({(e['asset'], e['decision_ts']) for e in events}),
                       qualified_markets=len(qualified), filled_markets=len(filled),
                       execution_reasons=dict(collections.Counter(e['execution_status'] for e in events if e.get('execution_status'))),
                       missed={t: dict(collections.Counter(e.get('execution_status') or e['status'] for e in events
                               if e.get('ticker') == t and e.get('z', 0) >= threshold)) for t in sorted(qualified-filled)})
result = dict(snapshot_et=dt.datetime.fromtimestamp(end, ET).isoformat(),
              period_hours=(end-start)/3600, after_activation=stats(post),
              equal_duration_before=stats([r for r in orders if start-(end-start) <= r['created_ts'] < start]),
              last24h=stats([r for r in orders if end-86400 <= r['created_ts'] <= end]),
              days={day: stats(rows) for day, rows in sorted(days.items())},
              all12_exchange_confirmed=confirmed,
              production_hashes_match=snapshot['production_hashes'] == activation['source_hashes'],
              wider_signal_entries=[r['ticker'] for r in post if r['signal']['signal_ask'] >= .93-1e-8],
              retry_entries=[r['ticker'] for r in post if r['signal'].get('execution_check_number', 1) > 1],
              lanes=lanes)
(ROOT / 'analysis.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k != 'lanes'}, indent=2))
