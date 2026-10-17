from pathlib import Path
import json,gzip,time,datetime as dt,requests
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(parents=True,exist_ok=True)
B='https://external-api.kalshi.com/trade-api/v2';START=1780444800;END=1791590400
def get(path,params=None):
 for n in range(5):
  r=requests.get(B+path,params=params,timeout=40)
  if r.status_code==200:return r.json()
  if r.status_code not in (429,500,502,503,504):raise RuntimeError((r.status_code,r.text[:200]))
  time.sleep(1+n)
 raise RuntimeError(path)
def save(path,x):
 if str(path).endswith('.gz'):
  with gzip.open(path,'wt') as f:json.dump(x,f)
 else:path.write_text(json.dumps(x,indent=2)+'\n')
def hour(m):
 t=m['ticker'];f=D/(t+'_1h.json.gz')
 if f.exists():return
 x=get('/margin/markets/'+t+'/candlesticks',{'start_ts':START,'end_ts':END,'period_interval':60});save(f,x)
 z=get('/margin/funding_rates/historical',{'ticker':t,'start_ts':START,'end_ts':END});save(D/(t+'_funding.json'),z)
 c=x.get('candlesticks',[]);print(t,'hours',len(c),'first',c[0]['end_period_ts'] if c else None,flush=True)
def minute(t):
 f=D/(t+'_1m.json.gz')
 if f.exists():return
 h=json.load(gzip.open(D/(t+'_1h.json.gz'),'rt')).get('candlesticks',[])
 if not h:save(f,{'candlesticks':[]});return
 start=min(x['end_period_ts'] for x in h)-3600;rows={}
 for s in range(start,END,3*86400):
  z=get('/margin/markets/'+t+'/candlesticks',{'start_ts':s,'end_ts':min(s+3*86400,END),'period_interval':1})
  if z.get('cursor'):raise RuntimeError('Unexpected pagination')
  for c in z.get('candlesticks',[]):rows[int(c['end_period_ts'])]=c
  time.sleep(.1)
 save(f,{'candlesticks':[rows[k] for k in sorted(rows)]});print(t,'minutes',len(rows),flush=True)
if __name__=='__main__':
 import sys
 mode=sys.argv[1] if len(sys.argv)>1 else'hour'
 if mode=='hour':
  m=get('/margin/markets');save(D/'markets.json',m);save(D/'risk_parameters.json',get('/margin/risk_parameters'))
  with ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(hour,m['markets']))
 else:
  ms=json.load(open(D/'markets.json'))['markets'];non=[m['ticker'] for m in ms if m['asset_class']!='Crypto' and m['ticker'].endswith('PERP')];alts=sorted([m for m in ms if m['asset_class']=='Crypto' and m['ticker'] not in ['KXBTCPERP','KXETHPERP'] and m['status']=='active'],key=lambda m:float(m.get('volume_24h_notional_value_dollars',0)),reverse=True)[:4];selected=non+[m['ticker'] for m in alts]
  save(P/'minute_liquidity_selection.json',{'noncrypto':non,'other_crypto_by_current_volume':[m['ticker'] for m in alts],'selected':selected})
  with ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(minute,selected))
