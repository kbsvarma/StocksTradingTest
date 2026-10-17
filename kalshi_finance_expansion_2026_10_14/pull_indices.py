"""Public-data index refresh; prior archives are read-only sources."""
from common import *
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
ROOT=P.parent
SERIES=('KXDJI','KXINXU','KXNASDAQ100U','KXINX','KXNASDAQ100','KXINXDUD','KXNASDAQDUD')
def pull(s):
 d=P/'indices'/s;d.mkdir(parents=True,exist_ok=True)
 catalog={x['ticker']:x for x in read(P/'data/series.json')['series']};save(d/'series.json',catalog[s])
 old=ROOT/'kalshi_noncrypto_consistency_2026_10_10/financial'/s
 if (d/'markets.json').exists():ms=read(d/'markets.json')
 else:
  prev=read(old/'markets.json') if (old/'markets.json').exists() else []
  lo=max((ts(x['close_time'])+1 for x in prev),default=START)
  fresh=paged('/markets',dict(series_ticker=s,status='settled',min_close_ts=lo,max_close_ts=END,limit=1000),'markets')
  ms=sorted({m['ticker']:m for m in prev+fresh if START<=ts(m['close_time'])<END and m.get('settlement_ts') and ts(m['settlement_ts'])<=END}.values(),key=lambda x:x['close_time']);save(d/'markets.json',ms)
 events=defaultdict(list)
 for m in ms:
  if m.get('strike_type') in ('greater','greater_or_equal','less','less_or_equal','between') and float(m.get('volume_fp') or 0)>=5:events[m['event_ticker']].append(m)
 errors=[]
 for i,(ev,mm) in enumerate(sorted(events.items())):
  cp=d/(ev+'_chart.json.gz');qp=d/(ev+'_candles.json.gz')
  try:
   if not cp.exists():
    if (old/cp.name).exists():cp.symlink_to(old/cp.name)
    else:save(cp,get('/live_data/events/'+ev,{'range':'1h'}))
   det=read(cp).get('live_data',{}).get('details',{})
   if not det.get('timeseries'):errors.append(dict(event=ev,error='no_source_chart'));continue
   if not qp.exists():
    if (old/qp.name).exists():qp.symlink_to(old/qp.name)
    else:
     cl=ts(mm[0]['close_time']);out={}
     for j in range(0,len(mm),100):
      x=get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in mm[j:j+100]),start_ts=cl-32*60,end_ts=cl,period_interval=1))
      out.update({r['market_ticker']:r['candlesticks'] for r in x.get('markets',[])})
     save(qp,out)
  except Exception as e:errors.append(dict(event=ev,error=str(e)))
  if i%20==0:print(s,i+1,'/',len(events),'errors',len(errors),flush=True)
 save(d/'receipt.json',dict(events=len(events),markets=len(ms),errors=errors,finished=time.time()));print('DONE',s,len(events),len(errors),flush=True)
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(pull,SERIES))
