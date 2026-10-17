import json,urllib.request,urllib.parse,time,concurrent.futures,datetime
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'data'
def get(url):
 for i in range(4):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'research/1.0'}),timeout=25) as f:return json.load(f)
  except Exception:
   if i==3:raise
   time.sleep(i+1)
products=get('https://api.exchange.coinbase.com/products');(D/'products.json').write_text(json.dumps(products));by={p['id']:p for p in products};assets=['DOGE','BNB','NEAR','ZEC','HYPE'];start=1788825600-7200;end=1790380800
print([(a,by.get(a+'-USD',{}).get('status','ABSENT')) for a in assets],flush=True)
def one(a):
 f=D/('spot_'+a+'.jsonl');done={json.loads(l)['start'] for l in f.read_text().splitlines()} if f.exists() else set()
 if a+'-USD' not in by:return
 with f.open('a') as out:
  for s in range(start,end,290*60):
   if s in done:continue
   q=urllib.parse.urlencode({'granularity':60,'start':s,'end':min(s+290*60,end)});r=get('https://api.exchange.coinbase.com/products/'+a+'-USD/candles?'+q)
   if not isinstance(r,list):raise ValueError(r)
   out.write(json.dumps({'start':s,'rows':r})+'\n');out.flush();time.sleep(.15)
 print(a,'complete',flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(one,assets))
