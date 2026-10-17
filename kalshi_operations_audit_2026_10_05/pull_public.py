"""Bounded GET-only backfill for the frozen operations audit, not a trader."""
from pathlib import Path
import datetime as dt
import json
import time
import requests

P = Path(__file__).resolve().parent
OUT = P / 'public'
OUT.mkdir(exist_ok=True)
BASE = 'https://api.elections.kalshi.com/trade-api/v2'
SESSION = requests.Session()
START = int(json.loads((P/'z3_receipt.json').read_text())['verified_ts']//60)*60
END = int(json.loads((P/'capture.json').read_text())['captured_ts']//60)*60
calls = []

def get(url, params, target):
    if target.exists():
        return json.loads(target.read_text())
    for attempt in range(4):
        begin = time.time()
        response = SESSION.get(url, params=params, timeout=15)
        calls.append(dict(url=url, params=params, status=response.status_code,
                          seconds=time.time()-begin, ts=time.time()))
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2**attempt)
            continue
        response.raise_for_status()
        data = response.json()
        target.write_text(json.dumps(data))
        time.sleep(.2)
        return data

def stamp(s):
    return int(dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())

markets = []
for asset in ('BTC', 'ETH', 'SOL', 'XRP'):
    cursor = ''
    page = 0
    while True:
        params = dict(series_ticker='KX'+asset+'15M', min_close_ts=START+1,
                      max_close_ts=END+900, limit=1000)
        if cursor:
            params['cursor'] = cursor
        data = get(BASE+'/markets', params, OUT/f'markets_{asset}_{page}.json')
        markets.extend(data['markets'])
        cursor = data.get('cursor')
        if not cursor:
            break
        page += 1
    for begin in range(START-120*60, END, 250*60):
        get(f'https://api.exchange.coinbase.com/products/{asset}-USD/candles',
            dict(granularity=60, start=begin, end=min(END, begin+250*60)),
            OUT/f'spot_{asset}_{begin}.json')
markets.sort(key=lambda m: (m['close_time'], m['ticker']))
(OUT/'markets_all.json').write_text(json.dumps(markets))
for i in range(0, len(markets), 40):
    batch = markets[i:i+40]
    lo = min(stamp(m['open_time']) for m in batch)
    hi = min(END, max(stamp(m['close_time']) for m in batch))
    if hi < lo:
        continue
    get(BASE+'/markets/candlesticks', dict(
        market_tickers=','.join(m['ticker'] for m in batch),
        start_ts=lo, end_ts=hi, period_interval=1), OUT/f'candles_{i:04}.json')
    print(json.dumps(dict(completed=min(i+40, len(markets)), total=len(markets))), flush=True)
(OUT/'requests.json').write_text(json.dumps(calls, indent=2))
print(json.dumps(dict(status='PASS', markets=len(markets), requests=len(calls),
                      start=START, end=END)), flush=True)
