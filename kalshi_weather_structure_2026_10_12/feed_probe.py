"""Six finite collection rounds; public reads only, no background service."""
from pathlib import Path
import json,time,urllib.request,urllib.parse,gzip,hashlib
P=Path(__file__).resolve().parent
CITIES={'KXTEMPMIAH':'miami','KXTEMPCHIHS':'chicago','KXTEMPLAXHS':'la-coastal','KXTEMPNYCHS':'nyc'}
def get(path,params):
    url='https://external-api.kalshi.com/trade-api/v2'+path+'?'+urllib.parse.urlencode(params)
    start=time.time()
    with urllib.request.urlopen(url,timeout=8) as response:body=json.load(response)
    received=time.time();record=dict(url=url,started=start,received=received,body=body)
    dest=P/'probe';dest.mkdir(exist_ok=True)
    with gzip.open(dest/(str(int(start*1000))+'.json.gz'),'wt') as f:json.dump(record,f)
    return record
for step in range(6):
    now=time.time();target=(now//60)*60+36
    if now>target:target+=60
    time.sleep(target-now)
    for series,city in CITIES.items():
        a=get('/live_data/weather/'+city,{'last_sec':900,'detailed':'true'})
        points=a['body']['timeseries'];pending=[v for v in points if v.get('v') is None and v.get('stations')]
        latest=max((v['t']/1000 for v in points if v.get('v') is not None),default=0)
        print(step,city,'canonical_age',round(a['received']-latest,2),'pending',len(pending),flush=True)
        time.sleep(1.6)
    # No book-value claims here: isolate source first-arrival and corrections.
    (P/'probe_progress.json').write_text(json.dumps({'rounds_complete':step+1,'last_received':time.time(),'orders':0}))
