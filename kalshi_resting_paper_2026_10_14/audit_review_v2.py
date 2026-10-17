"""Read-only receipt audit; writes compact evidence beside the latest review."""
import copy
import datetime as dt
import gzip
import hashlib
import json
import sqlite3
import statistics
import subprocess
import time
from pathlib import Path
from paper import apply_trade, canonical, fee, ioc_depth, number, timestamp
from observer import Observer

P = Path(__file__).resolve().parent
LIVE = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')


def validate_admission_queue(order, receipt):
    if order['variant'] == 'paper_ioc':
        return
    if order.get('price') is None:
        # A valid admission book can precede a design/risk skip. Such an order
        # never rested, so it has no price at which queue depth can be tested.
        assert order['state'] == 'skipped'
        assert all(number(order.get(k, 0)) == 0 for k in ('filled', 'cost', 'fees'))
        assert not order.get('fill_evidence')
        return
    own, _, _ = __import__('paper').book(receipt['body'], order['side'])
    assert order.get('queue_initial') is not None
    assert number(order['queue_initial']) == __import__('paper').queue_ahead(own, number(order['price']))


def run(root=P):
    root = Path(root)
    cfg = json.loads((root/'frozen_config.json').read_text())
    guard = Observer.__new__(Observer)
    guard.root, guard.cfg = root, cfg
    assert guard.source_guard() == '', 'Unreviewed live source revision'
    assert guard.flag_guard() == '', 'Unreviewed live flags'
    migration = json.loads((root/'reviewed_live_source_hashes.json').read_text())
    deployment = json.loads(Path(migration['migration']).read_text())
    expected = dict(cfg['live_source_hashes'], **migration['updates'])
    source_before = {n: hashlib.sha256((LIVE/n).read_bytes()).hexdigest() for n in expected}
    assert source_before == expected
    def service_snapshot():
        return subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service',
            'kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service',
            'kalshi-resting-paper.service','-p','Id','-p','MainPID','-p','ActiveState','-p','NRestarts'], text=True)
    services_before = service_snapshot()
    flags_before = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (LIVE/'state').iterdir()
        if f.is_file() and f.name.startswith(('ARM','HALT'))}
    dest = Path(json.loads((root/'latest_review.json').read_text())['path'])
    db = sqlite3.connect((root/'paper.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    meta = {k: json.loads(v) for k, v in db.execute('select key,value from meta')}
    orders = [json.loads(r[0]) for r in db.execute('select record from orders')]
    signals = [json.loads(r[0]) for r in db.execute('select record from signals')]
    actual = {t: json.loads(r) for t,r in db.execute('select ticker,record from actual')}
    logs = [dict(zip(('id','ts','kind','ticker','detail'),row)) for row in db.execute('select * from log')]
    receipts = {}
    for ident, digest, packed in db.execute('select id,digest,record from receipts'):
        raw = gzip.decompress(packed)
        assert hashlib.sha256(raw).hexdigest() == digest
        receipts[ident] = json.loads(raw)
    db.close()
    first = {}
    corrections = []
    for rid, r in receipts.items():
        if r['path'] != '/markets/trades' or not r.get('body'):
            continue
        for t in r['body'].get('trades', []):
            ident = t.get('trade_id')
            if not ident:
                continue
            if ident not in first:
                first[ident] = (t, rid, r['received'])
            elif canonical(t) != canonical(first[ident][0]):
                corrections.append(dict(trade_id=ident, first=first[ident][1], changed=rid))
    fills = []
    gaps = {}
    for o in orders:
        if o.get('admission_receipt'):
            times = sorted(r['received'] for r in receipts.values()
                           if r['path'] == '/markets/'+o['ticker']+'/orderbook'
                           and o['accepted_ts']-.25 <= r['received'] <= o['effective_expiry'])
            gaps[o['ticker']] = max((b-a for a, b in zip(times, times[1:])), default=0)
        if o.get('outcome_recorded'):
            m = receipts[o['settlement_receipt']]['body']['market']
            assert m['result'] == o['result'] and m['status'] in ('settled', 'finalized')
            assert number(o['payout']) == (number(o['filled']) if o['side'] == o['result'] else 0)
        if number(o['filled']) <= 0:
            continue
        if o['variant'] == 'paper_ioc':
            evidence = o['fill_evidence'][0]
            rec = receipts[evidence['receipt']]
            assert rec['started'] >= o['accepted_ts']
            assert rec['received'] < o['effective_expiry']
            assert ioc_depth(rec['body'], o['signal']) == evidence['levels']
            assert number(o['fees']) == fee(evidence['levels'], o['fee_meta']['multiplier'])
        else:
            replay = copy.deepcopy(o)
            replay.update(filled='0', queue_remaining=o['queue_initial'])
            for trade, rid, received in sorted(first.values(), key=lambda v: (v[0]['created_time'], v[0]['trade_id'])):
                apply_trade(replay, trade)
            assert number(replay['filled']) == number(o['filled'])
            for e in o['fill_evidence']:
                matches = [t for t in receipts[e['receipt']]['body']['trades'] if t['trade_id'] == e['trade_id']]
                assert len(matches) == 1
                t = matches[0]
                assert timestamp(t['created_time']) == e['exchange_ts']
                assert o['accepted_ts'] <= e['exchange_ts'] < o['effective_expiry']
                assert t['taker_side'] != o['side'] and t['is_block_trade'] is False
                assert number(t[o['side']+'_price_dollars']) <= number(o['price'])
                assert number(e['quantity']) <= number(t['count_fp'])
                fills.append(dict(ticker=o['ticker'], trade_id=e['trade_id'], quantity=e['quantity'],
                                  first_arrival_delay_seconds=e['first_received']-e['exchange_ts'],
                                  received_after_expiry=e['first_received'] > o['effective_expiry']))
    def stats(xs):
        return dict(n=len(xs), median=statistics.median(xs) if xs else None, max=max(xs) if xs else None)
    live_files = {n: hashlib.sha256((LIVE/n).read_bytes()).hexdigest() for n in expected}
    flags = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (LIVE/'state').iterdir()
             if f.is_file() and f.name.startswith(('ARM','HALT'))}
    services = service_snapshot()
    before_path = Path(json.loads((root/'latest_repair.json').read_text())['path'])
    before = json.loads((before_path/'before.json').read_text())
    assert live_files == source_before == expected
    assert flags == flags_before
    assert guard.flag_guard() == ''
    assert services == services_before
    assert hashlib.sha256((root/'frozen_config.json').read_bytes()).hexdigest() == before['paper_hashes']['frozen_config.json']
    for o in orders:
        key = o['variant']+'|'+o['ticker']
        if key in before['order_hashes']:
            assert hashlib.sha256(canonical(o).encode()).hexdigest() == before['order_hashes'][key]
    for asset, item in meta['series'].items():
        rec = receipts[item['receipt']]
        series = rec['body']['series']
        assert item['fee_type'] == series['fee_type']
        assert number(item['multiplier']) == number(series['fee_multiplier'])
        assert item['valid'] and time.time()-item['received'] < 25200
    for o in orders:
        if o.get('admission_receipt'):
            validate_admission_queue(o, receipts[o['admission_receipt']])
    cutover = deployment['verified_ts']
    segments = {}
    for label, predicate in [('before_websocket', lambda t: t < cutover), ('after_websocket', lambda t: t >= cutover)]:
        segments[label] = {}
        for variant in ['rest_3c_10s','rest_1c_10s','paper_ioc']:
            rows = [o for o in orders if o['variant']==variant and predicate(o['created_ts'])]
            comparable = [o for o in rows if o.get('outcome_recorded') and o.get('coverage_complete') and
                (o['ticker'] not in actual or number(actual[o['ticker']].get('filled',0))==0 or actual[o['ticker']].get('status')=='settled')]
            segments[label][variant] = dict(opportunities=len(rows), complete_settled=len(comparable),
                filled=sum(number(o['filled'])>0 for o in rows),
                all_recorded_paper_net=str(sum(number(o.get('pnl',0)) for o in rows)),
                comparable_paper_net=str(sum(number(o.get('pnl',0)) for o in comparable)),
                comparable_live_net=str(sum(number(actual.get(o['ticker'],{}).get('pnl',0)) for o in comparable)))
    (dest/'actual.json').write_text(json.dumps(actual,indent=2)+'\n')
    (dest/'signals.json').write_text(json.dumps(signals,indent=2)+'\n')
    (dest/'audit_log.json').write_text(json.dumps(logs,indent=2)+'\n')
    output = dict(ts=time.time(),status=json.loads((root/'status.json').read_text()),
                  receipt_hashes_verified=len(receipts),trade_ids=len(first),corrections=corrections,
                  http_429=sum(r['status']==429 for r in receipts.values()),
                  http_errors=sum(bool(r['error']) for r in receipts.values()),
                  pages_with_cursor=sum(bool((r.get('body') or {}).get('cursor')) for r in receipts.values() if r['path']=='/markets/trades'),
                  pagination_incomplete=sum(bool(o.get('trade_pagination_incomplete')) for o in orders),
                  max_book_gaps_seconds=gaps,resting_fill_receipts=fills,
                  signal_delivery_seconds=stats([s['paper_seen_ts']-s['live_saved_ts'] for s in signals]),
                  public_print_delay_seconds=stats([received-timestamp(t['created_time']) for t,rid,received in first.values()]),
                  fee_metadata=meta['series'],
                  validations=['Raw receipt SHA256, fill public-print direction/quantity/timestamps and queue replay PASS',
                               'IOC subsequent depth, fees and official outcome receipts PASS',
                               'All pre-pause-repair orders retained unchanged; reviewed WebSocket source revision, flags and service PIDs unchanged during audit',
                               'Admission queue depths and current fee metadata receipts verified',
                               'Frozen configuration unchanged; no real orders'],
                  production_source_hashes=live_files,production_flags=flags,production_services=services,
                  signal_to_accept_seconds=stats([o['accepted_ts']-o['signal'].get('signal_qualified_ts',o['signal']['observed_ts']) for o in orders if o['variant']=='rest_3c_10s' and o.get('accepted_ts')]),
                  websocket_cutover_ts=cutover,transport_segments=segments)
    (dest/'operations.json').write_text(json.dumps(output,indent=2)+'\n')
    # Prior repair evidence is immutable; save only in this review directory.
    print(json.dumps({k: output[k] for k in ('status','receipt_hashes_verified','trade_ids','corrections',
        'http_429','http_errors','pages_with_cursor','pagination_incomplete','max_book_gaps_seconds',
        'resting_fill_receipts','signal_delivery_seconds','public_print_delay_seconds','signal_to_accept_seconds','transport_segments','validations')},indent=2))


if __name__ == '__main__':
    run()
