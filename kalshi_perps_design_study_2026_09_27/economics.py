import sys,json,time,concurrent.futures,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'));from census import get
m=get('/margin/markets');(P/'data/markets.json').write_text(json.dumps(m));rates=json.loads((P.parent/'kalshi_full_review_2026_09_26/data/verified_perps_fee_rates.json').read_text())
def one(m):
 t=m['ticker'];d=get('/margin/markets/'+t+'/orderbook',{'depth':30});(P/'data'/(t+'_book.json')).write_text(json.dumps(d));b=d['orderbook'];asks=sorted((float(p),float(q)) for p,q in b['asks']);bids=sorted(((float(p),float(q)) for p,q in b['bids']),reverse=True)
 if not asks or not bids:return {'ticker':t,'unpriced':True}
 mid=(asks[0][0]+bids[0][0])/2;s=(asks[0][0]-bids[0][0])/mid*1e4;maker=rates['maker_fee_rates'][t]*1e4;taker=rates['taker_fee_rates'][t]*1e4
 path=P.parent/'kalshi_perps_expanded_2026_09_26/data'/(t+'_funding.json');f=json.loads(path.read_text())['funding_rates'];total=sum(float(x['funding_rate']) for x in f)
 return dict(ticker=t,volume24_dollars=m.get('volume_24h_notional_value_dollars'),spread_bp=s,maker_bp=maker,taker_bp=taker,maker_spread_less_fees_bp=s-2*maker,taker_roundtrip_bp=s+2*taker,best_ask=asks[0],best_bid=bids[0],funding_records=len(f),static25_short_funding_dollars=25*total,static25_long_funding_dollars=-25*total)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:r=list(ex.map(one,m['markets']))
(P/'economics.json').write_text(json.dumps(r,indent=2))
for x in r:print(json.dumps(x))
