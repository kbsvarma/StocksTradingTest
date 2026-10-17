"""Independent cash arithmetic and read-only production preservation check."""
from pathlib import Path
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
import gzip, json, hashlib, subprocess, time

P = Path(__file__).resolve().parent
R = P.parent
ledgers = json.load(gzip.open(P / 'selected_ledgers.json.gz', 'rt'))
orders = {x['ticker']: x for x in json.loads((R / 'kalshi_market_z_stops_2026_10_14/orders.json').read_text())}
checks = 0
for design, rows in ledgers.items():
    assert len(rows) == len({x['ticker'] for x in rows}) == 461
    for x in rows:
        o = orders[x['ticker']]
        assert D(o['filled']) * int(o['side'] == o['result']) - D(o['cost']) - D(o['fees']) == D(o['pnl'])
        assert x['hold_net'] == float(o['pnl'])
        if not x['exit']:
            assert x['net'] == x['hold_net']
            continue
        q, p = D(str(x['quantity'])), D(str(x['price']))
        fee = (D('.07') * q * p * (1-p)).quantize(D('.000001'), rounding=ROUND_CEILING)
        cash = (q*p-fee).quantize(D('.0001'), rounding=ROUND_FLOOR)
        net = cash - D(str(x['cost'])) - D(str(x['entry_fee'])) - q*D('.01')
        assert abs(float(net)-x['net']) < 1e-9
        assert abs(float(q*p-cash)-x['fee']) < 1e-9
        assert x['trigger']+1 < x['close']
        checks += 1
depth = json.loads((P / 'recorded_depth.json').read_text())
depth_checks = 0
for design, v in depth.items():
    for x in v['ledger']:
        if not x['exit']: continue
        levels = [(D(str(p)), D(str(q))) for p, q in x['levels']]
        assert sum(q for p, q in levels) == D(str(x['quantity']))
        gross = sum(p*q for p, q in levels)
        fee = sum((D('.07')*q*p*(1-p)).quantize(D('.000001'), rounding=ROUND_CEILING) for p, q in levels)
        cash = (gross-fee).quantize(D('.0001'), rounding=ROUND_FLOOR)
        net = cash-D(str(x['cost']))-D(str(x['entry_fee']))-D(str(x['friction']))
        assert abs(float(net)-x['net']) < 1e-9
        assert 0 <= x['trigger']-x['book_before'] <= .500001
        assert 0 <= x['book_after']-(x['trigger']+1) <= .500001
        assert x['book_after'] < x['close']
        depth_checks += 1
results = json.loads((P / 'detailed_results.json').read_text())
for v in results.values():
    for m in list(v['periods'].values())+list(v['assets'].values())+list(v['lanes'].values()):
        assert abs(m['hold']+m['loss_savings']-m['winner_cost']-m['net']) < 1e-8
        assert abs(m['fees_only_net']-m['net']-m['friction']) < 1e-8
    assert abs(sum(m['net'] for m in v['assets'].values())-v['periods']['full']['net']) < 1e-8

base = json.loads((R / 'kalshi_all_lane_exits_2026_10_14/preservation_after.json').read_text())
live = R / 'kalshi_revalidation_2026_09_12/live_v1'
code = {n: hashlib.sha256((live/n).read_bytes()).hexdigest() for n in base['hashes']}
flags = {n: hashlib.sha256((live/'state'/n).read_bytes()).hexdigest() for n in base['flags']}
services = {n: subprocess.check_output(['systemctl', '--user', 'show', n, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'NRestarts'], text=True) for n in base['services']}
comparison = dict(code_unchanged=code == base['hashes'], flags_unchanged=flags == base['flags'], service_states_pids_unchanged=services == base['services'])
assert all(comparison.values()), comparison
result = dict(checked_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), independent_quote_exit_cash_checks=checks, independent_depth_exit_cash_checks=depth_checks, all_461_original_ledger_identities=True, accounting_reconciled=True, production_files_checked=len(code), **comparison, financial_order_calls=False)
(P / 'cash_and_preservation_audit.json').write_text(json.dumps(result, indent=2)+'\n')
(P / 'preservation.json').write_text(json.dumps(dict(hashes=code, flags=flags, services=services), indent=2)+'\n')
print(json.dumps(result, indent=2))
