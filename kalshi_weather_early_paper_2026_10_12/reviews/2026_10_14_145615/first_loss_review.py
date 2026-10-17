"""Read-only, receipt-backed first-loss audit; no observer or strategy changes."""
from pathlib import Path
from decimal import Decimal
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zlib

R = Path(__file__).resolve().parent
P = R.parent.parent
sys.path.insert(0, str(P))
from engine import book, fee, payout, ts

a = json.loads((R / 'audit.json').read_text())
cfg = json.loads((P / 'frozen_config.json').read_text())
end = dt.datetime.fromisoformat(a['captured_utc']).timestamp()
db = sqlite3.connect((P / 'paper.sqlite').as_uri() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
db.execute('BEGIN')
def rows(query, args=()):
    return [dict(x) for x in db.execute(query, args)]
evidence = {}
def payload(rid):
    r = rows('SELECT * FROM receipts WHERE id=?', (rid,))[0]
    assert r['received'] <= end
    raw = zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?', (r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest() == r['hash']
    evidence[str(rid)] = {'receipt': r, 'payload': json.loads(raw)}
    return evidence[str(rid)]['payload']

pos = next(x for x in a['trades'] if x['id'] == 6)
assert pos == rows('SELECT * FROM positions WHERE id=6')[0]
d = rows('SELECT * FROM decisions WHERE id=525')[0]
pick = json.loads(d['data'])['pick']
attempts = rows('SELECT * FROM attempts WHERE decision=525 ORDER BY id')
checks = []
for attempt in attempts:
    ap = json.loads(attempt['data'])
    assert all(ap[k] == pick[k] for k in ('ticker', 'side', 'ask', 'cap', 'source'))
    assert ap['qty'] == 5 == cfg['contracts']
    assert ap['friction'] == cfg['friction'][attempt['scenario']] * 5
    bs = rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase', (attempt['id'],))
    rr = []
    for b in bs:
        raw = next(x for x in payload(b['receipt'])['orderbooks'] if x['ticker'] == pick['ticker'])
        calc, reason = book(raw, pick['side'], pick['cap'], 5)
        assert calc == json.loads(b['data'])['book'] and reason == b['reason']
        r = evidence[str(b['receipt'])]['receipt']
        assert r['status'] == 200 and not r['error']
        assert attempt['due'] <= r['started'] <= r['received'] <= attempt['due'] + cfg['execution_deadline']
        assert r['duration'] <= cfg['max_http'] and float(json.loads(r['headers']).get('Age') or 0) <= 1
        rr.append(r)
        checks.append({'attempt': attempt['id'], 'scenario': attempt['scenario'], 'phase': b['phase'], 'receipt': r['id'], 'book': calc, 'reason': reason})
    if attempt['scenario'] == 'early':
        assert len(rr) == 2 and all(x['book']['depth'] >= 5 for x in checks if x['attempt'] == attempt['id'])
        assert rr[1]['started'] - rr[0]['received'] >= cfg['second_book_seconds']
        assert rr[1]['received'] == pos['entered'] < pick['close']
        assert pos['price'] == .79 <= pick['cap'] == .80
        mr = json.loads(bs[0]['data'])['market_receipt']
        m = next(x for x in payload(mr)['markets'] if x['ticker'] == pick['ticker'])
        assert m['status'] == 'active' and not m.get('settlement_ts')
    elif attempt['scenario'] == 'delay60':
        assert attempt['reason'] == 'insufficient_depth_inside_cap' and len(bs) == 1
        assert checks[-1]['book']['depth'] == 0 and checks[-1]['book']['ask'] == .94
    else:
        pub = rows('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received LIMIT 1', (d['series'], d['source']))[0]
        assert not bs and attempt['reason'] == 'closed_before_execution' and pub['received'] > pick['close']

market = next(x for x in payload(pos['settlement_receipt'])['markets'] if x['ticker'] == pos['ticker'])
assert market['status'] == 'finalized' and market['result'] == 'yes' and market['settlement_ts']
assert float(market['expiration_value']) == 59.0 > pick['strike']
assert payout(market, pos['side']) * pos['qty'] == pos['payout'] == 0
assert pos['fee'] == fee(pos['price'], 5) and pos['friction'] == .05
assert Decimal(str(pos['cost'])) == Decimal('3.95') + Decimal(str(pos['fee'])) + Decimal(str(pos['friction']))
assert pos['net'] == -4.0581
trajectory = []
for source in rows('SELECT * FROM sources WHERE series=? AND source BETWEEN ? AND ? AND received<=? ORDER BY source,received', (d['series'], d['source'], pick['close'], end)):
    data = json.loads(source['data'])
    payload(source['receipt'])
    trajectory.append({'source': source['source'], 'received': source['received'], 'receipt': source['receipt'], 'pending': data['pending'], 'value': data['forecast'], 'censored': source['censored']})
assert trajectory[0]['value'] == 58.28 and trajectory[-1]['value'] == 59.0
settlement_polls = rows('SELECT * FROM receipts WHERE url LIKE ? AND received BETWEEN ? AND ? ORDER BY received', ('%/markets?tickers=' + pos['ticker'] + '%', pos['entered'], pos['settled']))
assert all(r['status'] == 200 and r['error'] is None for r in settlement_polls)
before = settlement_polls[-2]
previous_market = next(x for x in payload(before['id'])['markets'] if x['ticker'] == pos['ticker'])
assert not previous_market.get('settlement_ts')
out = {
    'captured_utc': a['captured_utc'], 'position': pos, 'signal': pick, 'checks': checks,
    'official_settlement': market, 'source_trajectory': trajectory,
    'settlement_detection_seconds_after_official': pos['settled'] - ts(market['settlement_ts']),
    'settlement_poll_count': len(settlement_polls), 'settlement_poll_errors': 0,
    'settlement_max_poll_gap_seconds': max(y['received'] - x['received'] for x, y in zip(settlement_polls, settlement_polls[1:])),
    'previous_settlement_poll': before['id'],
    'source_to_fill_seconds': pos['entered'] - pick['source']['first_seen'],
    'decision_to_fill_seconds': pos['entered'] - d['created'],
    'entry_breakeven_probability_after_costs': pos['cost'] / 5,
    'scenarios': a['portfolios'],
    'finding': 'The five-minute source value rose from58.28F to59.00F at expiry, crossing58.99F. Original source reconstruction matched its later publication; no same-minute correction. Depth-supported simulated entry obeyed frozen rules. This is a losing forecast, with no execution or settlement defect found. Delayed scenarios did not fill this same signal. Four outcomes cannot establish either an edge or its absence.',
    'limitation': 'Paper depth observations are not actual exchange fills; observed publication timing is bounded by polling. No post-hoc strategy adjustment or hypothetical exit profit included.',
    'receipts': evidence,
}
db.rollback()
db.close()
(R / 'first_loss_audit.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({k: v for k, v in out.items() if k not in ('receipts', 'signal', 'official_settlement', 'source_trajectory', 'scenarios')}, indent=2))
