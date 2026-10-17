"""Read-only account and decision audit; no order submission or live state writes."""
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

LIVE = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(LIVE))
from engine import Engine, Exchange, ACTIVE, number, crypto
from kalshi_client import Credentials, KalshiClientV2
from lanes import lane_for, NEW_ASSETS
from runner import live_permission

et = lambda t: datetime.fromtimestamp(t, ZoneInfo('America/New_York')).isoformat()
now = time.time()
status = json.loads((LIVE/'state/status.json').read_text())
starts = {}
for label, directory in [('three_new', 'kalshi_multiasset_2026_10_07'), ('all_eight', 'kalshi_wti_2026_10_07'), ('new_capacity', 'kalshi_capacity25_2026_10_07')]:
    starts[label] = json.loads((Path('/home/varma')/directory/'candidate/activation_receipt.json').read_text())['verified_ts']
with sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro', uri=True) as con:
    orders = [json.loads(x[0]) for x in con.execute('select record from orders')]
    events = [dict(json.loads(raw), event_ts=ts) for ts, raw in con.execute('select ts,event from events where ts>=? order by id', (min(starts.values()),))]
    integrity = con.execute('pragma quick_check').fetchone()[0]
client = KalshiClientV2(Credentials.load(), dry_run=True, timeout=5)
original_request = client._request
def get_only(method, *args, **kwargs):
    if method != 'GET':
        raise RuntimeError('Audit prohibits account mutations')
    return original_request(method, *args, **kwargs)
client._request = get_only
exchange = Exchange(client)
positions = {r['ticker']: abs(number(r, 'position_fp', 'position')) for r in exchange.positions() if crypto(r.get('ticker', '')) and abs(number(r, 'position_fp', 'position'))}
resting = [r for r in exchange.orders(status='resting') if crypto(r.get('ticker', ''))]
balances = {str(route): exchange.balance(route) for route in (0, 2)}
store = SimpleNamespace(all=lambda: orders, get=lambda ticker: next((r for r in orders if r['ticker'] == ticker), None))
engine = Engine(exchange, store, LIVE/'state', live=True, permission_check=live_permission)
guards = {s['asset']+'/'+s['lane']: engine.entry_guard(s, positions, resting, balances[str(s['exchange_index'])]) for s in status['scans'] if s.get('ticker') and 'exchange_index' in s}
source_expected = json.loads((Path('/home/varma/kalshi_capacity25_2026_10_07/candidate/staged_hashes.json')).read_text())
report = dict(checked_et=et(now), heartbeat_age_seconds=now-status['ts'], account_access='GET_ONLY', integrity=integrity,
              mode=status['mode'], armed=status['armed'], halt=engine.halt_reason(), error=status['error'],
              source_matches=all(hashlib.sha256((LIVE/n).read_bytes()).hexdigest()==h for n,h in source_expected.items()),
              limits={k:status[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget')},
              balances=balances, positions=positions, resting_orders=len(resting), active_ledger=sum(r['status'] in ACTIVE for r in orders),
              enabled_assets={a:s['enabled'] for a,s in status['assets'].items()}, entry_guards=guards, periods={})
for label, start in starts.items():
    es=[e for e in events if e['event_ts']>=start]
    first={}
    for e in es:
        if e.get('asset') and e.get('lane') and e.get('decision_ts'):
            first.setdefault((e['asset'],e['lane'],e['decision_ts']),e)
    qualifying={}
    for e in es:
        if not e.get('asset') or not e.get('lane') or not isinstance(e.get('z'),(int,float)): continue
        r=lane_for(e); t=e.get('remaining_minutes',-1); ask=e.get('signal_ask',0); bid=e.get('signal_bid',0)
        if r.min_minutes<=t<=r.max_minutes and float(r.floor)-1e-8<=ask<float(r.ceiling)-1e-8 and ask-bid<=.02000001 and e['z']>=r.threshold(t):
            key=e['ticker']+'/'+e['lane']
            q=qualifying.setdefault(key,dict(first_et=et(e['event_ts']), statuses=Counter(), executions=Counter()))
            q['statuses'][e.get('status','')]+=1; q['executions'][e.get('execution_status','')]+=1
    report['periods'][label]=dict(start_et=et(start),elapsed_minutes=(now-start)/60,
        first_decisions=len(first), statuses=dict(Counter(e.get('status') for e in first.values())),
        per_asset={a:dict(Counter(e.get('status') for e in first.values() if e['asset']==a)) for a in status['assets']},
        qualifying_markets=qualifying,
        new_orders=[dict(ticker=r['ticker'],asset=r['asset'],filled=r.get('filled'),status=r['status']) for r in orders if r['created_ts']>=start])
last=max((r for r in orders if Decimal(r.get('filled','0'))>0),key=lambda r:r['created_ts'])
remote=exchange.order(last['order_id'])
report['last_fill']=dict(ticker=last['ticker'],entry_et=et(last['created_ts']),filled=last['filled'],ledger_status=last['status'],pnl=last.get('pnl'),exchange_status=remote['status'],exchange_filled=remote.get('fill_count_fp'))
(OUT/'kalshi.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
print(json.dumps(report,indent=2,default=str))
