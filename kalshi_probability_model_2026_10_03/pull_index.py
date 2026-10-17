"""Bounded GET-only official-index history supplement, run on research server."""
from pathlib import Path
import datetime as dt, json, sys, time, hashlib
sys.path.insert(0, '/home/varma/kalshi_revalidation_2026_09_12/live_v1')
from kalshi_client import Credentials, KalshiClient
P=Path(__file__).resolve().parent
class ReadOnly(KalshiClient):
    def _request(self, method, path, **kwargs):
        assert method=='GET' and path=='/cfbenchmarks/history/values'
        return super()._request(method,path,**kwargs)
if __name__=='__main__':
    output=P/'inputs/index_supplement.jsonl';output.parent.mkdir(exist_ok=True)
    done={(r['asset'],r['hour']) for r in map(json.loads,output.read_text().splitlines())} if output.exists() else set()
    start=int(dt.datetime(2026,9,11,22,tzinfo=dt.timezone.utc).timestamp())
    end=int(dt.datetime(2026,10,4,tzinfo=dt.timezone.utc).timestamp())
    tasks=[('ETH',h) for h in range(start,end,3600)]+[('BTC',h) for h in range(end-86400-7200,end,3600)]
    client=ReadOnly(creds=Credentials.load(),dry_run=True)
    for i,(asset,hour) in enumerate(tasks):
        if (asset,hour) in done:continue
        before=time.monotonic()
        for attempt in range(3):
            try:
                response=client._request('GET','/cfbenchmarks/history/values',params={'id':{'BTC':'BRTI','ETH':'ETHUSD_RTI'}[asset], 'timespan':'HOUR','timestamp':dt.datetime.fromtimestamp(hour,dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')})
                raw=response.get('data',{})
                if raw.get('error'):raise ValueError('Benchmark upstream error')
                seconds={int(v['time'])//1000:float(v['value']) for v in raw.get('payload',[]) if int(v['time'])%1000==0 and hour<=int(v['time'])//1000<hour+3600}
                minutes=[]
                for t in range(hour,hour+3600,60):
                    values=[seconds[s] for s in range(t,t+60) if s in seconds]
                    if len(values)==60:minutes.append([t,values[-1],sum(values)/60])
                if not minutes:raise ValueError('No complete whole-second minutes')
                record=dict(asset=asset,hour=hour,seconds=len(seconds),minutes=minutes,retrieved_at=time.time(),payload_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest())
                with output.open('a') as f:f.write(json.dumps(record,separators=(',',':'))+'\n')
                break
            except Exception as exc:
                print('RETRY',asset,hour,type(exc).__name__,flush=True)
                if attempt==2:raise
                time.sleep(2**attempt)
        if i%25==0:print('progress',i+1,'of',len(tasks),asset,flush=True)
        time.sleep(max(0,1-(time.monotonic()-before)))
    print('COMPLETE',len(tasks),flush=True)
