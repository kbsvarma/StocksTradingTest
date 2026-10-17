"""Read-only live-decision audit; no order transport or configuration writes."""
import json
import math
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from operations_view import funnel

ET = ZoneInfo('America/New_York')
START = 1790993763.965353
now = time.time()
et = lambda t: datetime.fromtimestamp(t, ET).isoformat()
live = sqlite3.connect(f'file:{BASE}/state/orders.sqlite3?mode=ro', uri=True)
snapshot = sqlite3.connect(OUT / 'snapshot.sqlite3')
live.backup(snapshot)
live.close()
orders = [json.loads(r[0]) for r in snapshot.execute('SELECT record FROM orders')]
by_ticker = {r['ticker']: r for r in orders}
events = [dict(json.loads(e), event_ts=ts) for ts,e in snapshot.execute(
    'SELECT ts,event FROM events WHERE ts>=? ORDER BY id', (int(START // 60)*60,))]
status = json.loads((BASE / 'state/status.json').read_text())
out = dict(start_et=et(START), end_et=et(now), elapsed_days=(now-START)/86400,
           status={k: status.get(k) for k in ('ts','mode','armed','halted','error','lanes',
                       'execution_checks_max','execution_retry_deadline_seconds',
                       'max_ioc_submissions','max_contracts','max_risk','daily_loss_budget')},
           windows={}, daily={})

def stats(rows):
    filled = [r for r in rows if Decimal(r.get('filled','0')) > 0]
    settled = [r for r in filled if r['status'] == 'settled']
    return dict(attempted_markets=len(rows), filled_markets=len(filled), settled=len(settled),
                wins=sum(r['side'] == r.get('result') for r in settled),
                losses=sum(r['side'] != r.get('result') for r in settled),
                net=str(sum((Decimal(r['pnl']) for r in settled), Decimal(0))))

for label, since in [('since_activation',START),('since_prior_audit',1791174900),
                     ('last_24h',now-86400)]:
    ev = [e for e in events if e['event_ts'] >= since]
    first = {}
    for e in ev:
        if not e.get('kind') and e.get('lane') == 'premium_z3':
            first.setdefault((e.get('asset'),e.get('decision_ts')),e)
    win = [e for e in first.values() if 3 <= e.get('remaining_minutes',-1) <= 10]
    gates = Counter(e.get('status') for e in win)
    price_reasons = Counter()
    for e in win:
        if e.get('status') != 'price_gate': continue
        ask,bid=e.get('signal_ask'),e.get('signal_bid')
        if ask is None or bid is None: key='missing_quote'
        elif ask < .93-1e-8: key='below_93c'
        elif ask >= .98-1e-8: key='98c_or_more'
        else: key='spread_or_other'
        price_reasons[key] += 1
    out['windows'][label] = dict(start_et=et(since), funnel=funnel(ev,orders),
        z3_in_window_minutes=len(win), z3_first_statuses=dict(gates),
        z3_price_rejections=dict(price_reasons),
        actual={lane:stats([r for r in orders if r.get('lane','cushion_z2') == lane
                           and r['created_ts'] >= since])
                for lane in ('cushion_z2','premium_z3')})

for day in sorted({et(r['created_ts'])[:10] for r in orders if r['created_ts'] >= START}):
    out['daily'][day] = {lane:stats([r for r in orders
        if r['created_ts'] >= START and et(r['created_ts'])[:10] == day
        and r.get('lane','cushion_z2') == lane]) for lane in ('cushion_z2','premium_z3')}

qualified = defaultdict(list)
for e in events:
    if (e.get('lane') == 'premium_z3' and isinstance(e.get('z'),(int,float))
            and e['z'] >= 3 and .93-1e-8 <= e.get('signal_ask',0) < .98-1e-8
            and 3 <= e.get('remaining_minutes',-1) <= 10):
        qualified[e['ticker']].append(e)
out['qualified_traces'] = []
for ticker, es in qualified.items():
    r = by_ticker.get(ticker,{})
    out['qualified_traces'].append(dict(ticker=ticker,
        first_et=et(es[0]['event_ts']), last_et=et(es[-1]['event_ts']),
        minutes=len({e['decision_ts'] for e in es}), statuses=dict(Counter(e['status'] for e in es)),
        executions=dict(Counter(e.get('execution_status','') for e in es)),
        lane=r.get('lane'), order_status=r.get('status'), filled=r.get('filled'),pnl=r.get('pnl'),
        first_signal={k:es[0].get(k) for k in ('z','signal_ask','remaining_minutes','side')}))
out['z3_trades'] = [dict({k:r.get(k) for k in ('ticker','status','filled','pnl','asset',
    'entry','attempt_number')}, entry_et=et(r['created_ts'])) for r in orders
    if r.get('lane') == 'premium_z3']
out['operations_upgrade_activated'] = (Path('/home/varma/kalshi_operations_upgrade_2026_10_05/candidate/activation_receipt.json')).exists()
(OUT / 'audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('qualified_traces','status','z3_trades')},indent=2))
print('Z3_TRADES',json.dumps(out['z3_trades']))
print('MISSED',json.dumps([r for r in out['qualified_traces'] if not r.get('filled') or Decimal(r['filled']) == 0]))
