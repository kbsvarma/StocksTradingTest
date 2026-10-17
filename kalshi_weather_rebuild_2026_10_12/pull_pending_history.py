"""Bounded research-only historical station details, 64 public GET requests."""
from pathlib import Path
import sys,time,requests,hashlib
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_broad_review_2026_10_10'))
from common import save,read,ts
CITIES={'KXTEMPMIAH':'miami','KXTEMPCHIHS':'chicago','KXTEMPLAXHS':'la-coastal','KXTEMPNYCHS':'nyc'}
START=ts('2026-09-26T02:00:00Z');END=ts('2026-10-12T04:00:00Z')
session=requests.Session()
for series,city in CITIES.items():
    for lo in range(START,END,86400):
        dest=P/'pending_history'/series/(str(lo)+'.json.gz')
        if dest.exists():continue
        sec=time.time()%60
        if sec<36:time.sleep(36-sec)
        elif sec>54:time.sleep(60-sec+36)
        r=session.get('https://external-api.kalshi.com/trade-api/v2/live_data/weather/'+city,
            params={'from':lo*1000,'to':min(lo+86400,END)*1000,'detailed':'true'},timeout=18)
        r.raise_for_status();save(dest,{'url':r.url,'retrieved_ts':time.time(),'response':r.json()});time.sleep(1.5)
    print(series,'complete',flush=True)
save(P/'pending_pull_complete.json',{'completed_ts':time.time(),'start':START,'end':END,'days_per_city':len(list(range(START,END,86400))), 'cities':list(CITIES)})
