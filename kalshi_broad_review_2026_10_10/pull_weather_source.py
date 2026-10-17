from common import *
from collections import defaultdict
def main():
 for series in ['KXTEMPNYCHS','KXTEMPMIAH','KXTEMPLAXHS','KXTEMPCHIHS']:
  dest=P/'replays'/series;mp=dest/'markets.json'
  # Market fetch is harmless to duplicate when the candle worker has not started city.
  if not mp.exists():
   ms=paged('/markets',{'series_ticker':series,'status':'settled','min_close_ts':START,'max_close_ts':END,'limit':1000},'markets');save(mp,ms)
  ms=read(mp);selected={}
  # Observed endpoint returns ~12h of minute observations even for range=1h.
  # Request at each six-hour boundary to overlap and validate revisions.
  for m in sorted(ms,key=lambda x:x['close_time']):selected[ts(m['close_time'])//(6*3600)]=m
  for j,m in enumerate(selected.values()):
   out=dest/(m['event_ticker']+'_source.json.gz')
   if out.exists():continue
   try:
    x=get('/live_data/events/'+m['event_ticker'],{'range':'1h'});save(out,x)
   except Exception as e:save(dest/(m['event_ticker']+'_source_error.json'),{'error':str(e)})
  print('SOURCE',series,len(selected),flush=True)
if __name__=='__main__':main()
