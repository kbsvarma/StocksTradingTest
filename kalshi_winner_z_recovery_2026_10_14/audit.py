"""Independent read-only checks; writes only to this research directory."""
from pathlib import Path
import json, gzip, pickle, sqlite3, math, hashlib, time, subprocess
import numpy as np

P = Path(__file__).resolve().parent
R = P.parent
OLD = R / 'kalshi_all_lane_exits_2026_10_14'
LIVE = R / 'kalshi_revalidation_2026_09_12/live_v1'

def save(name, obj):
    (P / name).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')

rows = json.loads((P / 'winners.json').read_text())
paths = pickle.load(gzip.open(P / 'paths.pkl.gz', 'rb'))
inputs = pickle.load(gzip.open(P / 'inputs.pkl.gz', 'rb'))
orders = json.loads((OLD / 'all_orders.json').read_text())
orders += json.loads((P/'added_winners.json').read_text())
orders = {x['ticker']: x for x in orders}
assert len(rows) == len({x['ticker'] for x in rows}) == 438

with sqlite3.connect(f'file:{LIVE}/state/orders.sqlite3?mode=ro', uri=True) as conn:
    conn.execute('pragma query_only=on')
    live_orders = [json.loads(x[0]) for x in conn.execute('select record from orders')]
settled = [x for x in live_orders if x.get('status') == 'settled' and float(x.get('filled', 0)) > 0]
live_winners = {x['ticker']: x for x in settled if x['side'] == x['result'] and x['close_ts']<=1792029600}
studied = {x['ticker'] for x in rows}
assert studied == set(live_winners), 'Winner cohort changed; do not claim full current coverage.'
entry_fields = ['side', 'filled', 'created_ts', 'cost', 'fees', 'result', 'pnl']
for ticker in studied:
    assert all(live_winners[ticker].get(k) == orders[ticker].get(k) for k in entry_fields)
save('live_ledger_check.json', dict(checked_ts=time.time(), settled=len(settled),
    winners=len(live_winners), exact_winner_cohort_match=True,
    unchanged_entry_and_result_records=len(studied),
    winner_tickers_sha256=hashlib.sha256('\n'.join(sorted(studied)).encode()).hexdigest(),
    query_only=True))

formula_error = 0
threshold_checks = 0
source_dates = {}
worst_by_asset = {}
for r in rows:
    o = orders[r['ticker']]
    s = o['signal']
    expected = r['side'] * (s['spot'] / s['strike_proxy'] - 1) / (s['sigma'] * math.sqrt(s['remaining_minutes']))
    formula_error = max(formula_error, abs(expected - r['entry_z']))
    assert abs(expected - r['entry_z']) < 1e-8
    assert o['side'] == o['result']
    assert inputs['markets'][r['ticker']]['result'] == r['side']
    for method, tk, zk in [('primary','t','z'),('finest','finest_t','finest_z')]:
        t, z = paths[r['ticker']][tk], paths[r['ticker']][zk]
        assert np.isfinite(z).all() and np.all(np.diff(t) > 0)
        assert np.all((t > r['entered']) & (t < r['close']))
        assert float(z.min()) == r[method]['minimum']
        for k, v in r[method]['below'].items():
            assert bool((z < float(k)).any()) == v
            threshold_checks += 1
    a = r['asset']
    if a not in worst_by_asset or r['primary']['minimum'] < worst_by_asset[a]['primary']['minimum']:
        worst_by_asset[a] = r

# Check primary extrema directly against retained source receipts, not dense arrays.
source_checks = []
chart_roots = [R/'kalshi_metals_research_2026_10_11/data/charts',
    R/'kalshi_frequency_expansion_2026_10_12/financial_inputs/data/charts',
    R/'kalshi_finance_expansion_2026_10_14/fx/data/charts',
    R/'kalshi_loss_review_2026_10_14/noncrypto_data/charts',
    R/'kalshi_z_exit_research_2026_10_14/fresh', OLD/'fresh', P]
for a, r in worst_by_asset.items():
    e = r['nadir_evidence']; target = e['source_ts'] * 1000
    if a in ['BTC','ETH','SOL','XRP']:
        files = [OLD/'cf_seconds'/f'{a}_{e["source_ts"]//3600*3600}.json.gz']
    else:
        # Event identifiers drop the final contract suffix.
        event = r['ticker'].rsplit('-', 1)[0]
        files = sorted({f for root in chart_roots for f in root.glob(event[:-4]+'*') if f.name.endswith(('.json.gz','_chart.json'))})
    matches = []
    for f in files:
        if not f.exists():
            continue
        obj = json.load(gzip.open(f)) if f.suffix == '.gz' else json.loads(f.read_text())
        response = obj['response']
        if a in ['BTC','ETH','SOL','XRP']:
            data = [(x['time'], x['value']) for x in response['data']['payload']]
        else:
            data = [(x['t'], x['v']) for x in response['live_data']['details']['timeseries']]
        for ts, val in data:
            if int(ts) == target:
                assert float(val) == e['source_price']
                matches.append(dict(path=str(f.relative_to(R)), sha256=hashlib.sha256(f.read_bytes()).hexdigest(), value=float(val)))
    assert matches, f'Missing raw nadir receipt {a}'
    source_checks.append(dict(asset=a, ticker=r['ticker'], nadir=e, receipts=matches))
save('raw_nadir_checks.json', source_checks)

btc = next(x for x in json.loads((R/'kalshi_loss_review_2026_10_14/recent_data/markets_BTC.json').read_text()) if x['ticker']=='KXBTC15M-26OCT111030-30')
assert btc['result'] == 'no' and float(btc['expiration_value']) < btc['floor_strike']
save('btc_settlement_exception.json', dict(market=btc,
    strategy_proxy=orders[btc['ticker']]['signal']['strike_proxy'],
    note='Winning NO settlement is consistent with official strike and settlement average. Proxy-z did not rebound above zero. Do not discard this winner or claim a price recovery from its z nadir.'))

old = json.loads((OLD/'preservation_after.json').read_text())
hashes = {n:hashlib.sha256((LIVE/n).read_bytes()).hexdigest() for n in old['hashes']}
flags = {n:hashlib.sha256((LIVE/'state'/n).read_bytes()).hexdigest() for n in old['flags']}
services = {n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in old['services']}
assert hashes == old['hashes'] and flags == old['flags']
save('preservation_check.json', dict(ts=time.time(), live_code_identical=hashes==old['hashes'],
    flags_identical=flags==old['flags'], services_identical=services==old['services'], services=services))
save('audit.json', dict(winners=len(rows), entry_formula_max_error=formula_error,
    winner_exchange_result_matches=len(rows), exact_live_winner_matches=len(rows),
    independent_threshold_checks=threshold_checks, raw_asset_nadir_checks=len(source_checks),
    primary_observations=sum(r['primary']['observations'] for r in rows),
    finest_observations=sum(r['finest']['observations'] for r in rows),
    finest_crypto_max_gap=max(r['finest']['max_gap_seconds'] for r in rows if r['asset'] in ['BTC','ETH','SOL','XRP']),
    no_time_filters=True, no_order_book_or_exit_fill_claim=True))
print((P/'audit.json').read_text())
