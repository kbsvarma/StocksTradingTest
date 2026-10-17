from pathlib import Path
import requests,json,gzip,time,datetime,concurrent.futures
P=Path(__file__).resolve().parent;D=P/'extra_data';D.mkdir(exist_ok=True)
B='https://external-api.kalshi.com/trade-api/v2';end=1791590400;receipts=[]
def get(path,params):
 for n in range(5):
  z=requests.get(B+path,params=params,timeout=35)
  if z.status_code==200:return z.json()
  if z.status_code not in [429,500,502,503,504]:raise RuntimeError((z.status_code,z.text[:100]))
  time.sleep(n+1)
 raise RuntimeError('Retries exhausted')
def fetch(t):
 f=D/(t+'_1m.json.gz')
 if f.exists():return
 old=P/'dependencies/maker/inputs/expansion/data';hour=json.load(gzip.open(old/(t+'_1h.json.gz'),'rt'))['candlesticks'];start=min(x['end_period_ts'] for x in hour)-3600;rows={};requests_count=0
 for a in range(start,end,3*86400):
  x=get('/margin/markets/'+t+'/candlesticks',{'start_ts':a,'end_ts':min(a+3*86400,end),'period_interval':1});requests_count+=1
  if x.get('cursor'):raise RuntimeError('pagination')
  for c in x.get('candlesticks',[]):rows[int(c['end_period_ts'])]=c
  time.sleep(.15)
 with gzip.open(f,'wt') as w:json.dump({'candlesticks':[rows[k] for k in sorted(rows)]},w)
 fund=get('/margin/funding_rates/historical',{'ticker':t,'start_ts':start,'end_ts':end});(D/(t+'_funding.json')).write_text(json.dumps(fund));receipts.append(dict(ticker=t,minute_rows=len(rows),requested_start=start,end=end,requests=requests_count,source=B,retrieved=datetime.datetime.now(datetime.timezone.utc).isoformat()));(D/'receipts.json').write_text(json.dumps(receipts,indent=2));print(t,len(rows),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(fetch,['KXETHPERP','KXXRPPERP','KXBNBPERP','KXHYPEPERP','KXZECPERP']))
