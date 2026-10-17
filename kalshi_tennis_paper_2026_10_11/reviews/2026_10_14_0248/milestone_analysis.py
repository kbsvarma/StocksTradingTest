"""Descriptive first-milestone analysis of immutable prospective paper receipts."""
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from urllib.parse import urlparse

P = Path(__file__).resolve().parent
db = sqlite3.connect((P/'paper_snapshot.sqlite').as_uri()+'?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row
def rows(q, args=()):
    return [dict(x) for x in db.execute(q, args)]
audit = json.loads((P/'audit.json').read_text())
supp = json.loads((P/'supplemental_audit.json').read_text())
capture = json.loads((P/'capture.json').read_text())
assert hashlib.sha256((P/'paper_snapshot.sqlite').read_bytes()).hexdigest() == capture['snapshot_sha256']
positions = rows('SELECT * FROM positions ORDER BY settled,id')
attempts = {x['id']: x for x in rows('SELECT * FROM attempts')}
result = {'snapshot_utc': capture['captured_utc'], 'paper_only': True, 'scenarios': {}, 'pairs': [], 'normal_only': []}
for scenario in ['base','stress']:
    pp = [x for x in positions if x['scenario']==scenario]
    aa = [x for x in attempts.values() if x['scenario']==scenario]
    nn = [x['net'] for x in pp if x['settled'] is not None]
    rng = random.Random(20261014)
    boot = sorted(statistics.mean(rng.choices(nn,k=len(nn))) for _ in range(20000))
    checks = [x for x in audit['position_checks'] if x['scenario']==scenario]
    def bounds(key):
        xs = sorted(x[key] for x in checks)
        return {'min':min(xs),'median':statistics.median(xs),'p95':xs[int(.95*(len(xs)-1))],'max':max(xs)}
    result['scenarios'][scenario] = {
        'attempted_matches': len({a['event'] for a in aa}),
        'attempts':len(aa), 'settled_matches':len(nn),
        'mean_net_per_trade':statistics.mean(nn),
        'descriptive_iid_bootstrap_95pct_mean_net':[boot[500],boot[19499]],
        'uncertainty_limit':'Illustrative IID resampling only; ignores tournament/day dependence and strategy selection. Only three touched calendar dates, one fully elapsed, so no reliable cluster-level edge inference.',
        'decision_to_fill_seconds':bounds('decision_to_fill_seconds'),
        'minimum_depth_inside_cap':bounds('minimum_depth_inside_cap'),
        'first20_net':sum(nn[:20]) if len(nn)>=20 else None,
    }
for normal in [x for x in positions if x['scenario']=='base']:
    delayed = next((x for x in positions if x['scenario']=='stress' and x['event']==normal['event']),None)
    if delayed is None:
        result['normal_only'].append({'event':normal['event'],'normal_net':normal['net']})
        continue
    na, da = attempts[normal['attempt']], attempts[delayed['attempt']]
    result['pairs'].append({'event':normal['event'], 'same_decision':na['boundary']==da['boundary'],
        'same_ticker_side': (normal['ticker'],normal['side'])==(delayed['ticker'],delayed['side']),
        'normal_net':normal['net'],'delayed_net':delayed['net'],'delayed_minus_normal':delayed['net']-normal['net']})
result['pair_summary'] = {}
for key, pairs in [('same_decision',[x for x in result['pairs'] if x['same_decision']]),('different_decision',[x for x in result['pairs'] if not x['same_decision']])]:
    result['pair_summary'][key]={'count':len(pairs), 'normal_net':sum(x['normal_net'] for x in pairs),
        'delayed_net':sum(x['delayed_net'] for x in pairs),'delayed_minus_normal':sum(x['delayed_minus_normal'] for x in pairs)}
new_ids = {x['id'] for x in supp['new_http_failures']}
result['new_http_recoveries'] = [x for x in audit['http']['recoveries'] if x['receipt'] in new_ids]
result['new_http_types'] = dict(collections.Counter('candles' if x['boundary_utc'] else 'score' if 'game_stats' in x['event'] else 'catalogue' for x in result['new_http_recoveries']))
cutoff = dt.datetime.fromisoformat(supp['previous_review_utc']).timestamp()
result['new_empty_200_episodes'] = [x for x in supp['empty_200_payload_episodes_after_points'] if x['first_empty_received']>cutoff]
result['new_empty_decision_impact'] = [x for x in supp['empty_payload_decision_impact'] if x['boundary']>cutoff]
result['unpublished_candle_recovery'] = []
for h in [x for x in supp['new_health'] if x['kind']=='candle_not_published']:
    # Preserve exact diagnostics; a matched eventual decision is evidence only
    # when it was recorded for that same minute before the frozen deadline.
    boundary = int(h['at']//60)*60
    dd = rows('SELECT boundary,received,reason FROM decisions WHERE event=? AND boundary=?',(h['event'],boundary))
    result['unpublished_candle_recovery'].append({**h,'same_boundary_decision':dd})
(P/'milestone_analysis.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['TENNIS90 ALL SETTLED PAPER TRADES — one contract each; USD; fees and friction modeled',
       'Scenario | Market | Side | Entered UTC | Entry | Fee | Friction | Outcome | Net | Settlement receipt']
for p in positions:
    lines.append(f"{p['scenario']} | {p['ticker']} | {p['side']} | {dt.datetime.fromtimestamp(p['entered'],dt.timezone.utc).isoformat()} | {p['price']:.4f} | {p['fee']:.4f} | {p['friction']:.4f} | {'win' if p['payout']==1 else 'loss'} | {p['net']:+.4f} | {p['settlement_receipt']}")
(P/'TRADE_LEDGER.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['pairs','new_http_recoveries','unpublished_candle_recovery']},indent=2))
