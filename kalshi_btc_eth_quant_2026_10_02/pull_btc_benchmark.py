"""Read-only supplemental BTC benchmark history. Run on research host.

At most one request start per second; never touches trading state or submits an
order. Only exact whole-second values enter each completed minute.
"""
from pathlib import Path
import datetime as dt, json, sys, time
sys.path.insert(0, '/home/varma/kalshi_revalidation_2026_09_12/live_v1')
from kalshi_client import Credentials, KalshiClient

P=Path(__file__).resolve().parent/'fresh_data'
P.mkdir(exist_ok=True)
START=int(dt.datetime(2026,9,11,22,tzinfo=dt.timezone.utc).timestamp())
END=int(dt.datetime(2026,10,3,tzinfo=dt.timezone.utc).timestamp())

class ReadOnly(KalshiClient):
    def _request(self, method, path, **kwargs):
        assert method == 'GET' and path == '/cfbenchmarks/history/values'
        return super()._request(method,path,**kwargs)

if __name__=='__main__':
    path=P/'benchmark.jsonl'
    done={json.loads(l)['hour'] for l in path.open()} if path.exists() else set()
    client=ReadOnly(creds=Credentials.load(),dry_run=True)
    for i,hour in enumerate(t for t in range(START,END,3600) if t not in done):
        before=time.monotonic()
        try:
            d=client._request('GET','/cfbenchmarks/history/values',params={'id':'BRTI','timespan':'HOUR',
                'timestamp':dt.datetime.fromtimestamp(hour,dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')})
            raw=d.get('data',{})
            if raw.get('error'):raise ValueError('Benchmark returned error')
            seconds={int(v['time'])//1000:float(v['value']) for v in raw.get('payload',[])
                if int(v['time'])%1000==0 and hour<=int(v['time'])//1000<hour+3600}
            minutes=[]
            for t in range(hour,hour+3600,60):
                vals=[seconds[s] for s in range(t,t+60) if s in seconds]
                if len(vals)==60:minutes.append([t,vals[-1],sum(vals)/60])
            if not minutes:raise ValueError('No complete whole-second minutes')
            with path.open('a') as out:out.write(json.dumps(dict(asset='BTC',hour=hour,seconds=len(seconds),minutes=minutes),separators=(',',':'))+'\n')
            if i%25==0:print('hours_completed_this_run',i+1,flush=True)
        except Exception as exc:
            print('FAILED',hour,type(exc).__name__,str(exc)[:150],flush=True)
            # Avoid a persistent failure loop interfering with production.
            raise
        time.sleep(max(0,1-(time.monotonic()-before)))
