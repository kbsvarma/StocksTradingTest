"""Build fixed, pre-experiment residual distributions from existing read-only archives."""
import gzip,json,hashlib,datetime as dt
from pathlib import Path
from collections import defaultdict
from engine import CITIES,ts,reconstruct
P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_weather_rebuild_2026_10_12'
END=ts('2026-10-12T04:00:00Z');START=END-7*86400
inputs={};errors={};labels={};coverage={}
def read(path):
    b=path.read_bytes();inputs[str(path)]=hashlib.sha256(b).hexdigest()
    return json.loads(gzip.decompress(b) if path.suffix=='.gz' else b)
for series in CITIES:
    conf=read(OLD/'extension'/series/'calibrations.json')['calibrations'];raw={}
    for p in sorted((OLD/'pending_history'/series).glob('*.json.gz')):
        for a in read(p)['response']['timeseries']:
            t=a['t']/1000
            if START-600<=t<=END:raw[int(t)]=a
    ms=read(OLD/'extension'/series/'markets.json')
    earlier=P.parent/'kalshi_weather_validation_2026_10_10/data'/series/'markets.json'
    if not earlier.exists():earlier=P.parent/'kalshi_broad_review_2026_10_10/replays'/series/'markets.json'
    ms=list({m['ticker']:m for m in read(earlier)+ms}.values());events=defaultdict(list)
    for m in ms:
        if START<=ts(m['close_time'])<END:events[m['event_ticker']].append(m)
    out=defaultdict(list);lab=defaultdict(list)
    for event,items in sorted(events.items()):
        if any(not m.get('settlement_ts') or ts(m['settlement_ts'])>=END or m.get('expiration_value') is None or m.get('result') not in ('yes','no') for m in items):continue
        cl=ts(items[0]['close_time']);final=float(items[0]['expiration_value'])
        assert all((final>float(m['floor_strike']))==(m['result']=='yes') for m in items)
        for h in range(1,11):
            a=raw.get(int(cl-h*60))
            if not a:continue
            # No final QC disposition used to filter training observations.
            r,why=reconstruct(a,conf,cl,check_qc=False)
            if not r:continue
            out[str(h)].append(round(final-r['forecast'],5))
            lab[str(h)].append({'event':event,'source':r['source_t'],'forecast':r['forecast'],'final':final,'settled':max(ts(m['settlement_ts']) for m in items)})
    errors[series]=dict(out);labels[series]=dict(lab);coverage[series]={h:len(v) for h,v in out.items()}
result={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'start':START,'end':END,'errors':errors,'labels':labels,'coverage':coverage,'input_sha256':inputs,'selection':'All four cities; frozen exact source-to-expiry minutes; no forward retuning.'}
assert all(len(errors[s][str(h)])>=60 for s in CITIES for h in range(3,11))
(P/'training.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(coverage,indent=2))
