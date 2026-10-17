"""Prefix coverage for signals received after a three-second feed delay."""
import gzip, hashlib, json, pickle
import requests
from capture import P, B, get, save

data = pickle.load(gzip.open(B/'inputs.pkl.gz', 'rb'))
markets = json.load(gzip.open(P/'trades.json.gz'))
audit = json.loads((P/'capture_audit.json').read_text())
session = requests.Session()
receipts = [r for r in audit['receipts'] if '_prefix_' not in r['file']]
for i, r in enumerate(data['actual']):
    seen = {t['trade_id']:t for t in markets[r['ticker']]}
    cursor, cursors, page = '', set(), 0
    while True:
        params = dict(ticker=r['ticker'], min_ts=int(r['close'])-65,
                      max_ts=int(r['close'])-62, limit=1000, is_block_trade='false')
        if cursor:
            params['cursor'] = cursor
        dest = P/'public'/f"{r['ticker']}_prefix_{page:03d}.json.gz"
        x = get(session, 'https://external-api.kalshi.com/trade-api/v2/markets/trades', params, dest)
        for t in x['response']['trades']:
            assert t['ticker'] == r['ticker'] and not t.get('is_block_trade',False)
            if t['trade_id'] in seen:
                assert seen[t['trade_id']] == t
            seen[t['trade_id']] = t
        cursor = x['response'].get('cursor','')
        receipts.append(dict(file=dest.name, hash=hashlib.sha256(dest.read_bytes()).hexdigest(),
                             count=len(x['response']['trades']),cursor_present=bool(cursor)))
        page += 1
        if not cursor:
            break
        assert cursor not in cursors and page < 100
        cursors.add(cursor)
    markets[r['ticker']] = list(seen.values())
    print(i+1,'/111 prefix',r['ticker'],flush=True)
with gzip.open(P/'trades.json.gz','wt') as h:
    json.dump(markets,h)
audit.update(receipts=receipts,pages=len(receipts),trades=sum(map(len,markets.values())),
             window_start_seconds_before_close=65,all_cursors_exhausted=True)
audit['input_hashes'][str(P/'PLAN.txt')] = hashlib.sha256((P/'PLAN.txt').read_bytes()).hexdigest()
save('capture_audit.json',audit)
