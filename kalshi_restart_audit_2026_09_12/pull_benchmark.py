"""GET-only benchmark history; run on research host, outside production tree.

Retain exact whole-second minute close/average sufficient for minute rules.
"""
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,'/home/varmak/kalshi/kalshi_unbroken_bot')
from kalshi_client import Credentials, KalshiClient
ROOT=Path(__file__).resolve().parent/'fresh_data'
ROOT.mkdir(exist_ok=True)
START=int(dt.datetime(2026,8,13,tzinfo=dt.timezone.utc).timestamp())-7200
END=int(dt.datetime(2026,9,12,tzinfo=dt.timezone.utc).timestamp())
IDS={'BTC':'BRTI','ETH':'ETHUSD_RTI','SOL':'SOLUSD_RTI','XRP':'XRPUSD_RTI'}

def pull(task):
    asset,hour=task
    c=KalshiClient(creds=Credentials.load(),dry_run=True)
    for i in range(6):
        try:
            d=c._request('GET','/cfbenchmarks/history/values',params={
                'id':IDS[asset],'timespan':'HOUR',
                'timestamp':dt.datetime.fromtimestamp(hour,dt.timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')})
            raw=d.get('data',{})
            if raw.get('error'):raise RuntimeError(str(raw['error']))
            seconds={int(v['time'])//1000:float(v['value']) for v in raw.get('payload',[])
                     if int(v['time'])%1000==0 and hour<=int(v['time'])//1000<hour+3600}
            minutes=[]
            for t in range(hour,hour+3600,60):
                vals=[seconds[s] for s in range(t,t+60) if s in seconds]
                if len(vals)==60:minutes.append([t,vals[-1],sum(vals)/60])
            if not minutes:raise RuntimeError('no complete minutes')
            return dict(asset=asset,hour=hour,seconds=len(seconds),minutes=minutes)
        except Exception:
            if i==5:raise
            time.sleep(min(2**i,20))

if __name__=='__main__':
    path=ROOT/'benchmark.jsonl'
    done={(r['asset'],r['hour']) for r in map(json.loads,path.read_text().splitlines())} if path.exists() else set()
    tasks=[(a,t) for a in IDS for t in range(START,END,3600) if (a,t) not in done]
    print('benchmark hours',len(tasks),flush=True)
    with cf.ThreadPoolExecutor(max_workers=4) as pool,path.open('a') as out:
        futs={pool.submit(pull,t):t for t in tasks}
        for i,f in enumerate(cf.as_completed(futs)):
            try:r=f.result()
            except Exception as e:
                print('FAILED',futs[f],type(e).__name__,str(e)[:100],flush=True);continue
            out.write(json.dumps(r,separators=(',',':'))+'\n');out.flush()
            if i%100==0:print('benchmark completed',i+1,'/',len(tasks),flush=True)
