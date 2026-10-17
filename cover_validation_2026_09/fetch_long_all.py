from pathlib import Path
import pandas as pd,json,time,yfinance as yf,concurrent.futures
P=Path(__file__).resolve().parent;out=P/'long_daily';out.mkdir(exist_ok=True);sy=sorted(pd.read_parquet(P/'expanded_eligible_prices.parquet').columns);rows=[]
def fetch(s):
 f=out/(s+'.parquet')
 if f.exists():return dict(symbol=s,status='cached')
 try:
  d=yf.Ticker(s.replace('.','-')).history(start='2000-01-01',end='2026-09-12',auto_adjust=False,actions=True)
  if d.empty:return dict(symbol=s,status='empty')
  d.to_parquet(f);return dict(symbol=s,status='ok',rows=len(d))
 except Exception as e:return dict(symbol=s,status='error',error=str(e)[:180])
for off in range(0,len(sy),30):
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:r=list(ex.map(fetch,sy[off:off+30]))
 rows+=r;pd.DataFrame(rows).to_csv(P/'long_all_sources.csv',index=False)
 print('PROGRESS',off+len(r),len(sy),pd.Series([x['status'] for x in rows]).value_counts().to_dict(),flush=True)
 if sum(x['status'] in ['empty','error'] for x in r)>10:time.sleep(90)
 time.sleep(1)
print('DONE',len(rows),flush=True)
