import requests,json,time,datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent
urls={'kalshi_eth_book':'https://external-api.kalshi.com/trade-api/v2/margin/markets/KXETHPERP/orderbook?depth=20','kalshi_btc_book':'https://external-api.kalshi.com/trade-api/v2/margin/markets/KXBTCPERP/orderbook?depth=20','kalshi_markets':'https://external-api.kalshi.com/trade-api/v2/margin/markets?limit=100'}
for pid in ['ETP-20DEC30-CDE','BIP-20DEC30-CDE','ET-30OCT26-CDE','ET-27NOV26-CDE']:
 urls[pid+'_book']='https://api.coinbase.com/api/v3/brokerage/market/product_book?product_id='+pid+'&limit=20'
 urls[pid+'_meta']='https://api.coinbase.com/api/v3/brokerage/market/products/'+pid
def one(kv):
 k,u=kv;s=time.time();r=requests.get(u,timeout=25);e=time.time()
 try:b=r.json()
 except ValueError:b={'body':r.text[:300]}
 return k,{'url':u,'request_start':s,'response_received':e,'elapsed_seconds':e-s,'status':r.status_code,'data':b}
with ThreadPoolExecutor(max_workers=4) as ex:r=dict(ex.map(one,urls.items()))
(P/'data/current_quotes.json').write_text(json.dumps(r,indent=2)+'\n')
for k,v in r.items():
 d=v['data'];print(k,v['status'],round(v['elapsed_seconds'],2),str(d)[:450])
