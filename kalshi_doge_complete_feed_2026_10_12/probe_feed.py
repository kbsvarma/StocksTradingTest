"""Three-minute DOGE market-data subscription. No financial messages or daemon."""
import asyncio
import collections as C
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import websockets
sys.dont_write_bytecode=True
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'source'))
from kalshi_client import Credentials,_sign

async def main():
    creds=Credentials.load();ts=str(int(time.time()*1000));path='/trade-api/ws/v2'
    headers={'KALSHI-ACCESS-KEY':creds.key_id,'KALSHI-ACCESS-TIMESTAMP':ts,
        'KALSHI-ACCESS-SIGNATURE':_sign(creds.private_key,ts+'GET'+path)}
    subscription={'id':1,'cmd':'subscribe','params':{'channels':['cfbenchmarks_value','cfbenchmarks_value_5hz'],'index_ids':['DOGEUSD_RTI']}}
    started=time.time();counts=C.Counter();latencies=C.defaultdict(list);points=C.defaultdict(list);errors=[]
    with (P/'data/prospective_feed.jsonl').open('w') as f:
        async with websockets.connect('wss://external-api-ws.kalshi.com'+path,additional_headers=headers,ping_interval=20,ping_timeout=20) as ws:
            await ws.send(json.dumps(subscription))
            stop=time.monotonic()+185
            while time.monotonic()<stop:
                try:raw=await asyncio.wait_for(ws.recv(),timeout=min(10,max(.001,stop-time.monotonic())))
                except asyncio.TimeoutError:continue
                arrived=time.time();d=json.loads(raw);kind=d.get('type','unknown');counts[kind]+=1
                f.write(json.dumps({'received_at':arrived,'response':d},separators=(',',':'))+'\n');f.flush()
                if kind=='error':errors.append(d)
                if kind in ('cfbenchmarks_value','cfbenchmarks_value_5hz'):
                    m=d['msg'];v=m.get('data',{})
                    if isinstance(v,str):v=json.loads(v)
                    source=m.get('source_ts_ms',v.get('time'))
                    if source is not None:
                        source=int(source);points[kind].append(source);latencies[kind].append(arrived-source/1000)
    summary={}
    for kind,ps in points.items():
        lat=sorted(latencies[kind]);seconds=sorted(set(t//1000 for t in ps if t%1000==0))
        minute_counts=C.Counter(t//60*60 for t in seconds)
        full=list(range((min(ps)//60000+1)*60,max(ps)//60000*60,60))
        summary[kind]={'source_points':len(ps),'duplicate_source_timestamps':len(ps)-len(set(ps)),
            'latency_seconds':{'median':statistics.median(lat),'p95':lat[int((len(lat)-1)*.95)],'maximum':max(lat),'minimum':min(lat)},
            'interior_minutes':len(full),'interior_complete_60_second_minutes':sum(minute_counts[t]==60 for t in full),
            'max_source_gap_seconds':max((b-a)/1000 for a,b in zip(sorted(set(ps)),sorted(set(ps))[1:])) if len(ps)>1 else None}
    receipt={'started_unix':started,'finished_unix':time.time(),'counts':counts,'channels':summary,'errors':errors,
        'clock':subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','-p','Timezone'],text=True),
        'scope':'Short availability/arrival probe only, not a long-run reliability or execution test.'}
    (P/'feed_probe_summary.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt),flush=True)

if __name__=='__main__':asyncio.run(main())
