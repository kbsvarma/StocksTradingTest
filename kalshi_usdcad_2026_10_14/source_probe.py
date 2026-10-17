"""Public GET-only USD/CAD source readiness; no order or activation transport."""
import datetime as dt
import json
import sys
import time
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import market_data
from pyth_data import PythMinutes

def probe():
    started=time.time();p=PythMinutes();get=market_data.get;base=market_data.BASE
    p.warm('USDCAD',get,base)
    now=time.time();T=int(now//60)*60
    result=get(base+'/markets?series_ticker=KXUSDCAD15M&status=open&limit=100')
    stamp=lambda value:int(dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())
    m=next(m for m in result['markets'] if stamp(m['open_time'])<=now<stamp(m['close_time']))
    assert m['exchange_index']==0 and float(m['notional_value_dollars'])==1
    assert stamp(m['close_time'])-stamp(m['open_time'])==900
    assert m['price_level_structure']=='linear_cent'
    assert all(float(r['step'])==.01 for r in m['price_ranges'])
    sample=p.sample('USDCAD',m,T,get,base)
    assert sample is not None,'USD/CAD has incomplete completed-minute inputs; no activation'
    values,ref,evidence=sample
    assert len(values)==61 and evidence['bars_end']==T-60 and evidence['reference_ts']==stamp(m['open_time'])-60
    book=get(base+'/markets/'+m['ticker']+'/orderbook?depth=10')
    answer=dict(ts=time.time(),mode='PUBLIC_GET_ONLY',elapsed_seconds=time.time()-started,
        ticker=m['ticker'],exchange_index=0,reference=ref,spot=values[-1],evidence=evidence,
        price_ranges=m['price_ranges'],market=m,book=book,source_ready=True)
    return answer

if __name__=='__main__':
    answer=probe();(P/'source_probe.json').write_text(json.dumps(answer,indent=2)+'\n')
    print(json.dumps({k:answer[k] for k in ('ticker','exchange_index','source_ready','elapsed_seconds','evidence')}))
