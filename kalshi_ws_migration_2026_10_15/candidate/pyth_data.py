"""Completed Pyth minute closes from public event charts; no trading transport."""
import hashlib
import json
import math
import time
from urllib.parse import urlencode

class PythMinutes:
    def __init__(self):
        self.bars={};self.receipts={};self.frozen={};self.last_warm={}

    def absorb(self,asset,response,received):
        live=response.get('live_data',{})
        details=live.get('details',{})
        if details.get('asset')!=asset:raise ValueError('Underlying chart asset mismatch')
        points=details.get('timeseries')
        if not isinstance(points,list) or not points:raise ValueError('Underlying chart empty')
        incoming={}
        for point in points:
            stamp=int(point['t']);price=float(point['v'])
            if not math.isfinite(price) or price<=0:raise ValueError('Invalid underlying price')
            if stamp%60000!=59000:continue
            # The terminal second must already have passed at receipt time.
            if stamp/1000>received:continue
            minute=stamp//60000*60
            if minute in incoming and incoming[minute]!=price:raise ValueError('Conflicting chart prices')
            incoming[minute]=price
        bars=self.bars.setdefault(asset,{})
        # Latest published revision is usable for a NEW decision only. Once all
        # 61 inputs are complete, freeze that asset-minute across both lanes.
        bars.update(incoming)
        self.bars[asset]={k:v for k,v in bars.items() if k>=received-10800}
        self.receipts[asset]=received

    def warm(self,asset,get,base,now=None):
        now=time.time() if now is None else now
        if now-self.last_warm.get(asset,0)<30:return
        self.last_warm[asset]=now
        result=get(base+'/markets?'+urlencode(dict(series_ticker='KX'+asset+'15M',status='settled',limit=2)))
        markets=result.get('markets',[])
        if not markets:raise ValueError('No prior underlying chart for '+asset)
        # Two latest settled event charts overlap, but cover the extra minute
        # missing from a moving one-hour chart after a cold start.
        for market in markets:
            if not market.get('ticker','').startswith('KX'+asset+'15M-'):raise ValueError('Warmup series mismatch')
            raw=get(base+'/live_data/events/'+market['event_ticker']+'?range=1h')
            self.absorb(asset,raw,time.time())

    def sample(self,asset,market,T,get,base):
        key=(asset,T)
        if key in self.frozen:return self.frozen[key]
        raw=get(base+'/live_data/events/'+market['event_ticker']+'?range=1h')
        received=time.time();self.absorb(asset,raw,received)
        start=int(__import__('datetime').datetime.fromisoformat(market['open_time'].replace('Z','+00:00')).timestamp())
        keys=list(range(T-3660,T,60));bars=self.bars.get(asset,{})
        if start-60 not in bars or not all(k in bars for k in keys):return None
        values=[bars[k] for k in keys]
        evidence=dict(source='kalshi_event_pyth',received_ts=received,
                      latest_completed_second=(T-1),reference_ts=start-60,
                      bars_start=keys[0],bars_end=keys[-1],bars_count=61,
                      values_sha256=hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest())
        answer=(values,bars[start-60],evidence)
        self.frozen={k:v for k,v in self.frozen.items() if k[1]>=T-60}
        self.frozen[key]=answer
        return answer

PYTH=PythMinutes()
