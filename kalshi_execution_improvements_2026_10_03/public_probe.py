"""Bounded public-data smoke test of staged scanner/cache. No account or orders."""
from pathlib import Path
import json,sys,time

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import market_data
from runner import MinuteQuotes
from lanes import Z2,Z3

if __name__=='__main__':
    cache=MinuteQuotes(market_data.get);market_data.get=cache
    end=time.time()+125;last=None;rows=[]
    while time.time()<end:
        T=int(time.time()//60)*60
        if T!=last and 2<=time.time()-T<=15:
            cache.begin(T)
            for lane in (Z2,Z3):
                for asset in market_data.ASSETS:
                    try:r=market_data.scan(asset,T,set(),lane=lane)
                    except Exception as exc:r=dict(asset=asset,lane=lane,status='data_error',error=str(exc)[:150])
                    r['mode']='PUBLIC_GET_ONLY';rows.append(r)
            last=T
        time.sleep(.5)
    out=dict(real_orders_enabled=False,observations=len(rows),errors=[r for r in rows if r['status']=='data_error'],rows=rows,ts=time.time())
    (P/'public_probe.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(dict(real_orders_enabled=False,observations=len(rows),errors=len(out['errors']))))
