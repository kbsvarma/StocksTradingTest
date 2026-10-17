from common import *
from collections import Counter
out={}
for s in ['KXTEMPNYCHS','KXTEMPMIAH','KXTEMPLAXHS','KXTEMPCHIHS','KXAUDUSD15M','KXUSDCAD15M']:
 ms=paged('/markets',{'series_ticker':s,'min_close_ts':START,'max_close_ts':END,'limit':1000},'markets');ms=[m for m in ms if START<=ts(m['close_time'])<END]
 unknown=[m for m in ms if m.get('result') not in ('yes','no')];save(P/'completeness'/f'{s}_markets.json',ms)
 out[s]={'markets':len(ms),'events':len({m['event_ticker'] for m in ms}),'statuses':dict(Counter(m['status'] for m in ms)),'nonbinary_outcomes':len(unknown),'nonbinary_events':len({m['event_ticker'] for m in unknown}),'nonbinary_sample':[{k:m.get(k) for k in ('ticker','status','result','close_time','expiration_value')} for m in unknown[:8]]};print(s,out[s],flush=True)
save(P/'completeness.json',out)
