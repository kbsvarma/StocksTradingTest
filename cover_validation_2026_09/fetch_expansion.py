from pathlib import Path
import pandas as pd,yfinance as yf,json,time,concurrent.futures,logging
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
P=Path(__file__).resolve().parent;(P/'expanded_daily').mkdir(exist_ok=True);symbols=json.loads((P/'new_symbols.json').read_text());manifest=P/'expanded_sources.json';results=json.loads(manifest.read_text()) if manifest.exists() else {};rounds=0

def fetch(s):
 try:
  d=yf.Ticker(s.replace('.','-')).history(start='2017-01-01',end='2026-09-12',auto_adjust=False,actions=True,raise_errors=True)
  if d.empty:return s,{'status':'no_data'}
  d.to_parquet(P/'expanded_daily'/f'{s}.parquet');return s,{'status':'ok','rows':len(d),'start':str(d.index.min()),'end':str(d.index.max())}
 except Exception as e:
  msg=str(e)[:250];return s,{'status':'rate_limit' if 'Rate limit' in msg or 'Too Many' in msg else 'error','error':msg}

pending=[s for s in symbols if s not in results or results[s]['status']=='rate_limit']
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 while pending:
  batch=pending[:30];pending=pending[30:];rr=list(ex.map(fetch,batch));rate=[]
  for s,r in rr:
   results[s]=r
   if r['status']=='rate_limit':rate.append(s)
  manifest.write_text(json.dumps(results,indent=2));print('PROGRESS',len(results),'of',len(symbols),'ok',sum(v['status']=='ok' for v in results.values()),'remaining',len(pending)+len(rate),flush=True)
  if rate:
   pending=rate+pending;rounds+=1;pause=min(300,90+30*rounds);print('RATE PAUSE',pause,flush=True)
   for _ in range(pause//30):time.sleep(30)
  else:time.sleep(.25)
pd.DataFrame([dict(symbol=s,**r) for s,r in results.items()]).to_csv(P/'expanded_sources.csv',index=False);print('DONE',flush=True)
