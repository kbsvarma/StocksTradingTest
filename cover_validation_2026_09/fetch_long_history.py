from pathlib import Path
import pandas as pd,json,time,yfinance as yf
P=Path(__file__).resolve().parent;out=P/'long_daily';out.mkdir(exist_ok=True)
z=pd.concat([pd.read_csv(P/'verification_pool_extended.csv'),pd.read_csv(P/'verification_pool_expanded.csv')]);symbols=sorted(set(z.a)|set(z.b));rows=[]
for k,s in enumerate(symbols):
 f=out/(s+'.parquet')
 for attempt in range(5):
  try:
   if not f.exists():
    d=yf.Ticker(s.replace('.','-')).history(start='2000-01-01',end='2026-09-12',auto_adjust=False,actions=True)
    if d.empty:raise ValueError('empty history')
    d.to_parquet(f)
   d=pd.read_parquet(f);rows.append({'symbol':s,'status':'ok','first':str(d.index.min()),'last':str(d.index.max()),'rows':len(d)});break
  except Exception as e:
   if attempt==4:rows.append({'symbol':s,'status':'error','error':str(e)[:200]})
   else:time.sleep(30*(attempt+1))
 pd.DataFrame(rows).to_csv(P/'long_history_sources.csv',index=False)
 if k%20==0:print(k,len(symbols),flush=True)
 time.sleep(.2)
print('DONE',len(rows),flush=True)
