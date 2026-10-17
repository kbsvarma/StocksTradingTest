"""Bounded, public-GET-only smoke test; no credentials or engine construction."""
import argparse
import json
from pathlib import Path
import time
import market_data
from lanes import Z3

def main():
    p=argparse.ArgumentParser();p.add_argument('--minutes',type=float,default=2);a=p.parse_args()
    end=time.time()+min(5,max(.1,a.minutes))*60;last=None;rows=[]
    while time.time()<end:
        T=int(time.time()//60)*60
        if T!=last and 2<=time.time()-T<=15:
            for asset in market_data.ASSETS:
                try:r=market_data.scan(asset,T,set(),lane=Z3)
                except Exception as e:r=dict(asset=asset,status='data_error',error=str(e)[:200])
                r['probe_mode']='PUBLIC_GET_ONLY';rows.append(r)
                print(json.dumps({k:v for k,v in r.items() if k not in ('price_ranges','book_levels')}),flush=True)
            last=T
        time.sleep(1)
    result=dict(real_orders_enabled=False,observations=len(rows),errors=[r for r in rows if r['status']=='data_error'],rows=rows,finished_ts=time.time())
    (Path(__file__).parent/'probe_result.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
