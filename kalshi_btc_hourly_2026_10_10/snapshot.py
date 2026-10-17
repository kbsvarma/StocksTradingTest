"""Bounded GET-only market/source/book snapshot and production preservation."""
import sys,json,math,time,gzip,hashlib,importlib.util
from pathlib import Path
from collections import Counter
from pull import P,DATA,get,save,ts,START,END
def preservation():
 loc=P.parent/'kalshi_crypto_frequency_2026_10_09'
 spec=importlib.util.spec_from_file_location('deployed_helpers',loc/'deploy_resilience.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
 receipt=json.loads((loc/'deployment_receipt.json').read_text());n=h.validate_hashes(receipt['source_hashes'])
 state=json.loads((h.STATE/'status.json').read_text());assert h.configs(state)==receipt['config'] and h.flags()==receipt['flags']
 services={k:h.service(k) for k in h.SERVICES};assert all(v['ActiveState']=='active' for v in services.values())
 save(P/'preservation.json',dict(ts=time.time(),source_files=len(receipt['source_hashes']),stable_files=n,flags_unchanged=True,settings_unchanged=True,services=services,heartbeat_age=time.time()-state['ts'],mode=state['mode'],error=state['error'],halted=state['halted'],copper_paused=not state['assets']['COPPER']['enabled']))
def snapshot():
 inventories=[];books=[]
 for series in ('KXBTCD','KXBTC','KXETHD'):
  raw=get('/markets',dict(series_ticker=series,status='open',limit=1000));save(P/(series+'_open.json'),raw)
  ms=raw['response']['markets'];now=time.time();current=[m for m in ms if ts(m['open_time'])<=now<ts(m['close_time'])]
  if not current:inventories.append(dict(series=series,no_current=True));continue
  close=min(ts(m['close_time']) for m in current);current=[m for m in current if ts(m['close_time'])==close]
  old=json.loads((DATA/series/'markets.json').read_text());recent=[m for m in old if END-86400<=ts(m['close_time'])<END]
  inventories.append(dict(series=series,asof=time.time(),current_expiry=close,minutes_remaining=(close-time.time())/60,strikes=len(current),current_contract_volume=sum(float(m['volume_fp'] or 0) for m in current),previous_ET_day_contract_volume=sum(float(m['volume_fp'] or 0) for m in recent),previous_ET_day_expiries=len({m['event_ticker'] for m in recent}),sample_rules=[{k:current[0].get(k) for k in ('ticker','rules_primary','rules_secondary','exchange_index','strike_type','price_ranges')}]))
  def fav(m):
   b,a=float(m['yes_bid_dollars']),float(m['yes_ask_dollars']);return (1,b,a) if b+a>1 else (-1,1-a,1-b)
  # Prefer quote-band matches; only public evidence, never transmit an order.
  selected=sorted(current,key=lambda m:(abs(fav(m)[2]-.92),fav(m)[2]-fav(m)[1],-float(m['volume_fp'] or 0),m['ticker']))[:4]
  for m in selected:
   book=get('/markets/'+m['ticker']+'/orderbook');save(P/('book_'+m['ticker']+'.json'),book)
   ob=book['response'].get('orderbook_fp',{});side,bid,ask=fav(m)
   levels=sorted((1-float(p),float(q)) for p,q in ob.get('no_dollars' if side==1 else 'yes_dollars',[]) if float(q)>0)
   books.append(dict(series=series,ticker=m['ticker'],side=side,bid=bid,ask=ask,spread=ask-bid,minutes_remaining=(close-book['received_ts'])/60,levels=levels,quantity_at_93c=sum(q for p,q in levels if p<=.93000001),quantity_at_97c=sum(q for p,q in levels if p<=.97000001),received_ts=book['received_ts']))
 save(P/'market_snapshot.json',dict(inventories=inventories,books=books,read_only=True))
 print('snapshot',[(r['series'],r.get('strikes'),r.get('previous_ET_day_contract_volume')) for r in inventories],flush=True)
if __name__=='__main__':preservation();snapshot()
