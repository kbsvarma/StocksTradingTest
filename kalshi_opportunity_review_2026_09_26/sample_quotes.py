import json,urllib.request,urllib.parse,datetime,concurrent.futures,statistics,time
from pathlib import Path
P=Path(__file__).resolve().parent
series='KXGOLD15M KXWTI15M KXSILVER15M KXCOPPER15M KXNATGAS15M KXPALLADIUM15M KXPLATINUM15M KXEURUSD15M KXGBPUSD15M KXUSDJPY15M'.split()
def ts(t):return int(datetime.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp())
def run(t):
 ms=json.loads((P/'data'/(t+'.json')).read_text())['settled'];ms=sorted([m for m in ms if float(m.get('volume_fp') or 0)>0],key=lambda m:m['close_time']);sample=[ms[round(i*(len(ms)-1)/7)] for i in range(8)];out=[]
 cachepath=P/'data'/(t+'_quote_sample.json');cache={r['ticker']:r for r in json.loads(cachepath.read_text())} if cachepath.exists() else {}
 for m in sample:
  if m['ticker'] in cache and 'error' not in cache[m['ticker']]:
   out.append(cache[m['ticker']]);continue
  q=urllib.parse.urlencode({'start_ts':ts(m['open_time']),'end_ts':ts(m['close_time']),'period_interval':1})
  url='https://api.elections.kalshi.com/trade-api/v2/series/'+t+'/markets/'+m['ticker']+'/candlesticks?'+q
  try:
   for retry in range(5):
    try:
     with urllib.request.urlopen(url,timeout=25) as f:r=json.load(f)
     break
    except Exception:
     if retry==4:raise
     time.sleep(3*(retry+1))
   out.append({'ticker':m['ticker'],'candles':r.get('candlesticks',[])})
  except Exception as e:out.append({'ticker':m['ticker'],'error':str(e)})
  time.sleep(1.1)
 (P/'data'/(t+'_quote_sample.json')).write_text(json.dumps(out))
 spreads=[];qual=0;valid=0
 for row,m in zip(out,sample):
  qualifies=False
  for c in row.get('candles',[]):
   left=(ts(m['close_time'])-c['end_period_ts'])/60
   if not 3<=left<=10:continue
   try:a=float(c['yes_ask']['close_dollars']);b=float(c['yes_bid']['close_dollars'])
   except (KeyError,ValueError,TypeError):continue
   if 0<b<=a<1:
    spreads.append((a-b)*100);valid+=1
    fav=a if (a+b)/2>=.5 else 1-b
    if a-b<=.0200001 and .85<=fav<.93:qualifies=True
  qual+=qualifies
 return {'ticker':t,'sampled_markets':len(sample),'candles_available':sum(bool(x.get('candles')) for x in out),'valid_window_quotes':valid,'median_spread_cents':round(statistics.median(spreads),3) if spreads else None,'markets_with_price_spread_window_match':qual,'errors':[x for x in out if 'error' in x]}
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:r=list(ex.map(run,series))
(P/'quote_sample_summary.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
