from common import *
from collections import Counter
SERIES=[f'KXNFL{i}Q' for i in range(1,5)]+[f'KXNHL{i}P' for i in range(1,4)]
allseries=get('/series',{'category':'Sports'})
save(P/'sports_series.json',allseries)
for s in SERIES:
    info=get('/series/'+s)['series'];save(P/'catalogue'/(s+'_series.json'),info)
    current=paged('/markets',{'series_ticker':s,'status':'settled','limit':1000},'markets')
    historical=paged('/historical/markets',{'series_ticker':s,'limit':1000},'markets')
    ms=list({m['ticker']:m for m in historical+current}.values())
    save(P/'catalogue'/(s+'_markets.json'),ms)
    print(s,'markets',len(ms),'events',len({m['event_ticker'] for m in ms}),
          'earliest',min((m['event_ticker'] for m in ms),default=None),
          'latest',max((m['event_ticker'] for m in ms),default=None),flush=True)
    if ms:save(P/'catalogue'/(s+'_example.json'),ms[-1])
