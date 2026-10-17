"""Isolated GET-only source collection. Nothing is imported from live runner."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / 'source'))
from kalshi_client import Credentials, KalshiClient

START = int(dt.datetime(2026, 9, 30, 22, tzinfo=dt.timezone.utc).timestamp())
END = int(dt.datetime(2026, 10, 12, 5, tzinfo=dt.timezone.utc).timestamp())

class ReadOnly(KalshiClient):
    def _request(self, method, path, **kwargs):
        assert method == 'GET' and path == '/cfbenchmarks/history/values'
        return super()._request(method, path, **kwargs)

def main():
    rawdir = P / 'data/index_hours'
    rawdir.mkdir(parents=True, exist_ok=True)
    client = ReadOnly(creds=Credentials.load(), base_url='https://external-api.kalshi.com', dry_run=True, timeout=12)
    hours = list(range(START, END, 3600))
    for i, hour in enumerate(hours):
        path = rawdir / f'{hour}.json.gz'
        if path.exists():
            continue
        # Keep research requests outside live scanner's first 32 seconds/minute.
        phase = time.time() % 60
        if phase < 32 or phase >= 58:
            time.sleep((32 - phase) % 60)
        before = time.monotonic()
        request = {'id':'DOGEUSD_RTI', 'timespan':'HOUR',
                   'timestamp':dt.datetime.fromtimestamp(hour, dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')}
        response = client._request('GET', '/cfbenchmarks/history/values', params=request)
        raw = response.get('data', {})
        if raw.get('error') or not isinstance(raw.get('payload'), list) or not raw['payload']:
            raise ValueError('Missing benchmark payload; see isolated response')
        receipt = {'hour':hour, 'request':request, 'received_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                   'elapsed_seconds':time.monotonic()-before, 'response':response,
                   'response_sha256':hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest()}
        with gzip.open(path.with_suffix('.tmp'), 'wt') as f:
            json.dump(receipt, f, separators=(',', ':'))
        path.with_suffix('.tmp').replace(path)
        if i % 20 == 0:
            print(json.dumps({'progress':i+1, 'total':len(hours), 'rows':len(raw['payload']), 'hour':hour}), flush=True)
        time.sleep(max(0, 1 - (time.monotonic()-before)))
    print('COMPLETE', len(hours), flush=True)

if __name__ == '__main__':
    main()
