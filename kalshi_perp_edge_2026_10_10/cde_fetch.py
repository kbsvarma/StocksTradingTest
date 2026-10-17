import json,requests,datetime as dt,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent;D=P/'data/cde';D.mkdir(exist_ok=True,parents=True)
B='https://api.coinbase.com/api/v3/brokerage/market/products/'
products=[a+'-'+d+'-CDE' for a in ['ET','BIT'] for d in ['26JUN26','31JUL26','28AUG26','25SEP26','30OCT26','27NOV26','24DEC26']]
def get(u,params=None):
 for k in range(4):
  r=requests.get(u,params=params,timeout=30)
  if r.status_code==200:return r.json()
  if r.status_code==429:time.sleep(1+k);continue
  return {'error_status':r.status_code,'message':r.text[:300]}
 return {'error_status':429}
def one(pid):
 meta=get(B+pid);(D/(pid+'_meta.json')).write_text(json.dumps(meta));detail=meta.get('future_product_details',{});exp=detail.get('contract_expiry')
 print('CDE',pid,meta.get('error_status'),exp,flush=True)
 if not exp:return
 end=int(dt.datetime.fromisoformat(exp.replace('Z','+00:00')).timestamp());end=min(end,int(dt.datetime(2026,10,10,tzinfo=dt.timezone.utc).timestamp()));start=max(end-100*86400,int(dt.datetime(2026,6,3,tzinfo=dt.timezone.utc).timestamp()));out={};s=start;errors=[]
 while s<end:
  en=min(s+280*3600,end);res=get(B+pid+'/candles',{'start':s,'end':en,'granularity':'ONE_HOUR','limit':350})
  (D/(pid+'_'+str(s)+'.json')).write_text(json.dumps(res))
  for c in res.get('candles',[]):out[int(c['start'])]=c
  if 'error_status'in res:errors.append(res);break
  s=en;time.sleep(.2)
 (D/(pid+'_candles.json')).write_text(json.dumps({'rows':[out[k] for k in sorted(out)],'errors':errors,'start':start,'end':end}));print('CDE_CANDLES',pid,len(out),len(errors),flush=True)
with ThreadPoolExecutor(max_workers=2) as ex:list(ex.map(one,products))
