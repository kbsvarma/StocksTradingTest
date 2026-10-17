"""Fetch archived NBS forecasts from Iowa State, never brokerage endpoints."""
from pathlib import Path
import urllib.request,urllib.parse,urllib.error,json,gzip,time
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent
STATIONS=['KNYC','KMDW','KMIA','KLAX','KPHL','KATL','KDCA','KPHX']
def one(station):
    dest=P/'forecasts'/(station+'.json.gz');dest.parent.mkdir(exist_ok=True)
    if dest.exists():return
    args={'station':station,'model':'NBS','sts':'2026-08-14T00:00Z','ets':'2026-10-10T00:00Z','format':'json'}
    url='https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py?'+urllib.parse.urlencode(args)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=55) as r:raw=r.read()
            break
        except urllib.error.HTTPError as error:
            if error.code!=429 or attempt==2:raise
            delay=max(10,min(60,int(error.headers.get('Retry-After','30'))))
            print(station,'rate limit; waiting',delay,flush=True);time.sleep(delay)
    rows=json.loads(raw);assert isinstance(rows,list)
    with gzip.open(dest,'wt') as f:json.dump(rows,f)
    (dest.parent/(station+'_receipt.json')).write_text(json.dumps({'url':url,'retrieved_ts':time.time(),'rows':len(rows),'bytes':len(raw)},indent=2))
    print(station,len(rows),flush=True)
for station in STATIONS:
    one(station);time.sleep(3)
