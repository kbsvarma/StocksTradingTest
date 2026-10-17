import json,time,urllib.request,urllib.parse,concurrent.futures,statistics,datetime
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'data';D.mkdir(exist_ok=True)
S=json.loads((P/'series.json').read_text())['series']; lookup={x['ticker']:x for x in S}
selected={x['ticker'] for x in S if x['category'] in ['Financials','Commodities','Climate and Weather'] and x['frequency'] in ['hourly','fifteen_min']}
selected.update('KXGOLDD KXSILVERD KXWTI KXBRENTD KXCOPPERD KXNATGASD KXINX KXNASDAQ100 KXTNOTED KXHIGHNY KXHIGHCHI KXHIGHMIA KXHIGHLAX KXRAINNYC KXAAAGASD KXAAAGASW KXTSAW KXFEDDECISION KXCPI KXBTCD KXETHD KXBTC15M KXETH15M KXXRP15M KXSOL15M KXDOGE15M KXHYPE15M KXBNB15M KXZEC15M KXNEAR15M KXMLBGAME KXNFLGAME KXWNBAGAME KXATPMATCH KXWTAMATCH KXCS2GAME'.split())
START=int(datetime.datetime(2026,9,19,tzinfo=datetime.timezone.utc).timestamp());END=int(datetime.datetime(2026,9,26,tzinfo=datetime.timezone.utc).timestamp())
def get(path,params):
 url='https://api.elections.kalshi.com/trade-api/v2'+path+'?'+urllib.parse.urlencode(params)
 for k in range(5):
  try:
   with urllib.request.urlopen(url,timeout=30) as r:return json.load(r)
  except Exception:
   if k==4:raise
   time.sleep(1+k)
def scan(t):
 try:
  data={};truncated={}
  for status in ['open','settled']:
   rows=[];cursor=''
   for page in range(40):
    p={'series_ticker':t,'status':status,'limit':1000}
    if status=='settled':p.update(min_settled_ts=START,max_settled_ts=END)
    if cursor:p['cursor']=cursor
    r=get('/markets',p);rows+=r['markets'];cursor=r.get('cursor','')
    if not cursor:break
   data[status]=rows;truncated[status]=bool(cursor)
  (D/(t+'.json')).write_text(json.dumps(data))
  op=data['open'];st=data['settled'];v=[float(x.get('volume_fp') or 0) for x in st];sp=[];tight=0
  for x in op:
   b=float(x.get('yes_bid_dollars') or 0);a=float(x.get('yes_ask_dollars') or 1)
   if 0<b<=a<1:
    sp.append(round((a-b)*100,3))
    if a-b<=.02000001 and min(float(x.get('yes_bid_size_fp') or 0),float(x.get('yes_ask_size_fp') or 0))>=2:tight+=1
  z={'ticker':t,'title':lookup.get(t,{}).get('title'),'category':lookup.get(t,{}).get('category'),'frequency':lookup.get(t,{}).get('frequency'),'fee_multiplier':lookup.get(t,{}).get('fee_multiplier'),'open':len(op),'open_24h_contracts':sum(float(x.get('volume_24h_fp') or 0) for x in op),'quoted':len(sp),'median_spread_cents':statistics.median(sp) if sp else None,'tight_2ct':tight,'settled_7d':len(st),'traded_7d':sum(a>0 for a in v),'contracts_7d':sum(v),'median_contracts':statistics.median(v) if v else 0,'truncated':truncated}
  print(t,len(op),len(st),sum(v),flush=True);return z
 except Exception as e:return {'ticker':t,'error':str(e)}
selected={'KXBTCD','KXETHD','KXNASDAQ100U'}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:out=list(ex.map(scan,sorted(selected)))
(P/'refresh_summary.json').write_text(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'window':'2026-09-19 inclusive to 2026-09-26 exclusive UTC; settlement time','series_count':len(S),'results':out},indent=2))
print('COMPLETE',len(out),flush=True)
