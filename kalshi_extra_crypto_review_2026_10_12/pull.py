"""Resumable public-GET-only commodity replay inputs. No account/order client."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request

P = Path(__file__).resolve().parent
ROOT = P.parent
DATA = P / 'data'
DATA.mkdir(exist_ok=True)
BASE = 'https://external-api.kalshi.com/trade-api/v2'
ASSETS = ('DOGE','HYPE','NEAR','ZEC','BNB','ADA','BCH','TON')

def ts(s):
    s = re.sub(r'\.(\d+)', lambda m: '.'+m.group(1)[:6].ljust(6, '0'), s)
    return int(dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())

START = ts('2026-10-01T00:00:00Z')
END = ts('2026-10-12T04:30:00Z')
lock = threading.Lock()
next_request = 0.

def get(path, params=None):
    global next_request
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(5):
        with lock:
            pause = max(0., next_request - time.monotonic())
            next_request = max(time.monotonic(), next_request) + .8
        if pause:
            time.sleep(pause)
        try:
            req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'commodity-frozen-replay/1.0'})
            with urllib.request.urlopen(req, timeout=25) as response:
                body = response.read()
            return {'retrieved_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'url': url, 'sha256_response': hashlib.sha256(body).hexdigest(),
                    'response': json.loads(body)}
        except Exception:
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 8))

def write(path, obj):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, separators=(',', ':')) + '\n')
    tmp.replace(path)

def markets(asset):
    path = DATA / f'markets_{asset}.json'
    if path.exists():
        return json.loads(path.read_text())
    series = 'KX' + asset + '15M'
    write(DATA / f'series_{asset}.json', get('/series/' + series))
    rows = []
    cursor = ''
    seen = set()
    pages = []
    while True:
        q = dict(series_ticker=series, status='settled', limit=1000,
                 min_close_ts=START-7200, max_close_ts=END)
        if cursor:
            q['cursor'] = cursor
        d = get('/markets', q)
        pages.append(d)
        rows.extend(m for m in d['response']['markets']
                    if START-7200 <= ts(m['close_time']) < END)
        cursor = d['response'].get('cursor')
        if not cursor:
            break
        if cursor in seen:
            raise ValueError('pagination loop')
        seen.add(cursor)
    rows = sorted({m['ticker']: m for m in rows}.values(), key=lambda m: m['close_time'])
    write(DATA / f'market_pages_{asset}.json', pages)
    write(path, rows)
    print(asset, 'markets', len(rows), flush=True)
    return rows

def candles(asset, rows):
    path = DATA / f'candles_{asset}.jsonl'
    prior = ROOT / 'kalshi_full_review_2026_09_26/candidate_data' / f'KX{asset}15M_candles.jsonl'
    legacy = ROOT / 'kalshi_commodity_validation_2026_09_25/data' / f'candles_{asset}.jsonl'
    wanted = {m['ticker'] for m in rows}
    old = {}
    previous_z = ROOT / 'kalshi_commodity_transfer_2026_10_02/data' / f'candles_{asset}.jsonl'
    for source in (legacy, prior, previous_z, ROOT / "kalshi_noncrypto15m_2026_10_07/data" / path.name, ROOT / "kalshi_market_expansion_2026_10_09/crypto/data" / path.name, path):
        if source.exists():
            for line in source.open():
                r = json.loads(line)
                if r['ticker'] in wanted and r.get('candles'):
                    old[r['ticker']] = r
    path.write_text(''.join(json.dumps(r, separators=(',', ':'))+'\n' for r in old.values()))
    todo = [m for m in rows if m['ticker'] not in old]
    groups = []
    group = []
    for m in todo:
        trial = group + [m]
        span = (ts(trial[-1]['close_time'])-ts(trial[0]['open_time']))//60 + 2
        if group and (len(trial) > 20 or len(trial)*span > 8000):
            groups.append(group)
            group = []
        group.append(m)
    if group:
        groups.append(group)
    print(asset, 'cached candles', len(old), 'remaining', len(todo), 'batches', len(groups), flush=True)
    rawdir = DATA / 'raw_candles'
    rawdir.mkdir(exist_ok=True)
    for i, group in enumerate(groups):
        q = dict(market_tickers=','.join(m['ticker'] for m in group),
                 start_ts=ts(group[0]['open_time']), end_ts=ts(group[-1]['close_time']), period_interval=1)
        r = get('/markets/candlesticks', q)
        with gzip.open(rawdir / (group[0]['ticker']+'.json.gz'), 'wt') as f:
            json.dump(r, f, separators=(',', ':'))
        by = {x['market_ticker']: x['candlesticks'] for x in r['response'].get('markets', [])}
        with path.open('a') as f:
            for m in group:
                cs = []
                for c in by.get(m['ticker'], []):
                    item = {'t': c['end_period_ts'], 'v': c.get('volume_fp')}
                    for name, prefix in [('yes_bid', 'b'), ('yes_ask', 'a')]:
                        for field, suffix in [('open', 'o'), ('close', 'c'), ('high', 'h'), ('low', 'l')]:
                            item[prefix+suffix] = c.get(name, {}).get(field+'_dollars')
                    cs.append(item)
                f.write(json.dumps({'ticker': m['ticker'], 'candles': cs})+'\n')
        if i % 10 == 0:
            print(asset, 'candles batch', i+1, '/', len(groups), flush=True)

def chart(m):
    event = m['event_ticker']
    path = DATA / 'charts' / (event+'.json.gz')
    if path.exists():
        return
    prior = ROOT / 'kalshi_noncrypto15m_2026_10_07/data/charts' / path.name
    if not prior.exists():prior = ROOT / 'kalshi_commodity_transfer_2026_10_02/data/charts' / path.name
    if prior.exists():
        shutil.copyfile(prior,path)
        return
    r = get('/live_data/events/'+event, {'range': '1h'})
    live = r['response'].get('live_data', {})
    details = live.get('details', {})
    if details.get('event_ticker') != event or not details.get('timeseries'):
        raise ValueError('missing/mismatched chart '+event)
    with gzip.open(path.with_suffix('.tmp'), 'wt') as f:
        json.dump(r, f, separators=(',', ':'))
    path.with_suffix('.tmp').replace(path)

def main():
    allrows = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(markets, a): a for a in ASSETS}
        for f in as_completed(futures):
            allrows[futures[f]] = f.result()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(candles, a, allrows[a]) for a in ASSETS]
        for f in as_completed(futures):
            f.result()
    # Each chart provides the preceding hour. Fetch each hour's last market,
    # plus the session-ending partial hour. Other source times are never filled.
    chosen = []
    for a in ASSETS:
        byhour = {}
        for m in allrows[a]:
            byhour[(ts(m['close_time'])-1)//3600] = m
        chosen.extend(byhour.values())
    (DATA/'charts').mkdir(exist_ok=True)
    write(DATA/'chart_selection.json', [m['event_ticker'] for m in chosen])
    print('charts selected', len(chosen), flush=True)
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(chart, m): m for m in chosen}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                f.result()
            except Exception as e:
                errors.append({'event': futures[f]['event_ticker'], 'error': str(e)})
            if i % 100 == 0:
                print('charts', i, '/', len(chosen), 'errors', len(errors), flush=True)
    write(DATA/'pull_receipt.json', {'finished_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
          'markets': {a: len(allrows[a]) for a in ASSETS}, 'charts': len(chosen), 'errors': errors,
          'source': BASE, 'reads_only': True})
    print('DONE', len(chosen), 'charts, errors', len(errors), flush=True)

if __name__ == '__main__':
    main()
