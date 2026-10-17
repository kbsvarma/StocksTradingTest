"""Capture public final-minute receipts; never loads production or credentials."""
import datetime as dt
import gzip
import hashlib
import json
import pickle
import time
from pathlib import Path
import requests

P = Path(__file__).resolve().parent
B = P.parent / 'kalshi_wti_sell_floors_2026_10_15'

def save(name, value):
    (P / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

def get(session, url, params, dest):
    if dest.exists():
        result = json.load(gzip.open(dest))
        assert result['params'] == params
        return result
    for attempt in range(4):
        started = time.time()
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code in [429, 500, 502, 503, 504]:
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
            result = dict(started=started, received=time.time(), url=r.url,
                          params=params, status=r.status_code, response=r.json())
            with gzip.open(dest, 'wt') as h:
                json.dump(result, h)
            time.sleep(.2)
            return result
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError('GET failed: ' + url)

def main():
    data = pickle.load(gzip.open(B / 'inputs.pkl.gz', 'rb'))
    rows = data['actual']
    assert len(rows) == 111 and sum(r['won'] for r in rows) == 106
    (P / 'public').mkdir(exist_ok=True)
    session = requests.Session()
    receipts, markets = [], {}
    for i, row in enumerate(rows):
        seen, cursors, cursor, page = {}, set(), '', 0
        while True:
            params = dict(ticker=row['ticker'], min_ts=int(row['close']) - 62,
                          max_ts=int(row['close']), limit=1000, is_block_trade='false')
            if cursor:
                params['cursor'] = cursor
            dest = P / 'public' / f"{row['ticker']}_{page:03d}.json.gz"
            result = get(session, 'https://external-api.kalshi.com/trade-api/v2/markets/trades', params, dest)
            for tr in result['response']['trades']:
                assert tr['ticker'] == row['ticker'] and not tr.get('is_block_trade', False)
                if tr['trade_id'] in seen:
                    assert seen[tr['trade_id']] == tr
                seen[tr['trade_id']] = tr
            cursor = result['response'].get('cursor', '')
            receipts.append(dict(file=dest.name, hash=hashlib.sha256(dest.read_bytes()).hexdigest(),
                                 count=len(result['response']['trades']), cursor_present=bool(cursor)))
            page += 1
            if not cursor:
                break
            assert cursor not in cursors and page < 100
            cursors.add(cursor)
        markets[row['ticker']] = list(seen.values())
        print(i + 1, '/111', row['ticker'], len(seen), 'trades', page, 'pages', flush=True)
    with gzip.open(P / 'trades.json.gz', 'wt') as h:
        json.dump(markets, h)
    series = get(session, 'https://external-api.kalshi.com/trade-api/v2/series/KXWTI15M', {}, P/'public'/'series.json.gz')
    save('capture_audit.json', dict(cohort=111, markets=len(markets),
         pages=len(receipts), trades=sum(map(len, markets.values())), receipts=receipts,
         all_cursors_exhausted=True, captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
         input_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in [B/'inputs.pkl.gz', B/'seconds.pkl.gz', B/'frozen_orders.json', P/'PLAN.txt']}))

if __name__ == '__main__':
    main()
