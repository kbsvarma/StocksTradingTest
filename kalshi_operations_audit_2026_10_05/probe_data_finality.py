"""Three-minute public GET-only probe of early/late completed candle values.

No credentials, private endpoints, bot source changes, or trading process import.
"""
import json, time, math, statistics
from pathlib import Path
import requests

P=Path(__file__).resolve().parent
session=requests.Session()
headers=('Age','Date','Cache-Control','CF-Cache-Status','X-Cache')
rows=[]
start=(int(time.time()//60)+1)*60
for T in range(start,start+3*60,60):
    for offset in (2,10,22,40):
        while time.time()<T+offset:
            time.sleep(min(1,T+offset-time.time()))
        for asset in ('BTC','ETH','SOL','XRP'):
            for lookback in (95,96):
                url=f'https://api.exchange.coinbase.com/products/{asset}-USD/candles'
                request_ts=time.time()
                try:
                    response=session.get(url,params=dict(granularity=60,start=T-lookback*60,end=T),timeout=3)
                    response.raise_for_status();raw=response.json()
                    bars={int(r[0]):float(r[4]) for r in raw if int(r[0])<=T-60}
                    vals=[bars[k] for k in range(T-61*60,T,60)]
                    sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
                    rows.append(dict(asset=asset,decision_ts=T,planned_offset=offset,lookback=lookback,
                        request_ts=request_ts,received_ts=time.time(),spot=bars[T-60],sigma=sigma,
                        headers={k:response.headers.get(k) for k in headers},bars=bars))
                except Exception as exc:
                    rows.append(dict(asset=asset,decision_ts=T,planned_offset=offset,lookback=lookback,
                        request_ts=request_ts,received_ts=time.time(),error=str(exc)))
        (P/'data_finality_probe.json').write_text(json.dumps(dict(GET_ONLY=True,completed=False,start=start,rows=rows),indent=2)+'\n')
        print(json.dumps(dict(T=T,offset=offset,observations=len(rows))),flush=True)
(P/'data_finality_probe.json').write_text(json.dumps(dict(GET_ONLY=True,completed=True,start=start,rows=rows),indent=2)+'\n')
