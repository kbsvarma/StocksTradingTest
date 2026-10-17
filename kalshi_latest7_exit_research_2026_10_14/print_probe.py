"""Independent public prints near selected exits; never labeled executable bids."""
from pathlib import Path
import json,gzip,requests,time,datetime
P=Path(__file__).resolve().parent
ledger=json.load(gzip.open(P/'selected_ledgers.json.gz','rt'))['BTC_WTI_NATGAS_additions'];s=requests.Session();out=[]
for r in ledger:
 if r['won'] or not r['exit'] or r['close']<1791777600:continue
 start=r['trigger']+1;end=min(start+10,r['close']);p=P/'public'/(r['ticker']+'_selected_exit_prints.json')
 if not p.exists():
  st=time.time();v=s.get('https://external-api.kalshi.com/trade-api/v2/markets/trades',params=dict(ticker=r['ticker'],min_ts=start,max_ts=end,limit=1000),timeout=15)
  p.write_text(json.dumps(dict(started=st,received=time.time(),url=v.url,status=v.status_code,response=v.json()))+'\n')
 receipt=json.loads(p.read_text());trades=receipt.get('response',{}).get('trades',[])
 # Determine held side from the original frozen ledger, not from results.
 original=next(o for o in json.loads((P.parent/'kalshi_market_z_stops_2026_10_14/orders.json').read_text()) if o['ticker']==r['ticker']);side=original['side']
 prices=[float(t[side+'_price_dollars']) for t in trades]
 out.append(dict(ticker=r['ticker'],trigger=r['trigger'],price_model=r['price'],query_start=start,query_end=end,status=receipt.get('status'),prints=len(trades),truncated=bool(receipt.get('response',{}).get('cursor')),min_print=min(prices) if prices else None,max_print=max(prices) if prices else None,contracts_printed=sum(float(t['count_fp']) for t in trades),note='Public transaction prices are not book depth or proof our sell would fill.'))
(P/'selected_print_probe.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
