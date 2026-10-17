"""Independently rebuild dense entry books from original stream receipts."""
from pathlib import Path
import json,pickle,gzip,collections
P=Path(__file__).resolve().parent;R=P.parent
def fixed(s,n=4):return round(float(s)*10**n)
def main():
 manifest={int(x['file'].split('_')[1]):x for x in json.loads((R/'kalshi_scalp_feasibility_2026_10_14/capture_manifest.json').read_text()) if x['accepted']}
 targets=collections.defaultdict(dict)
 ll=json.load(gzip.open(P/'dense_stream_ledgers.json.gz','rt'))
 for label in ('60s_delay1_friction0','1s_delay1_friction0'):
  for x in ll[label]:
   m=manifest[int(x['close'])];bundles=pickle.load(gzip.open(P/'dense'/m['target'],'rb'));b=next(v for v in bundles if v['market']['ticker']==x['ticker'])
   targets[int(x['close'])][(round(x['entered']*1000),x['ticker'])]=b['books'][x['book_index']]
 receipts=[]
 for close,expected in targets.items():
  books={};found=set();lastms=max(t for t,ticker in expected)
  with gzip.open(R/'kalshi_btc_eth_quant_2026_10_02/forward_state/data'/manifest[close]['file'],'rb') as f:
   for line in f:
    r=json.loads(line);now=r['client_received_at_ms'];msg=r.get('msg',{});kind=r['type']
    for key in [(now,ticker) for ticker in books if (now,ticker) in expected and (now,ticker) not in found]:
     ticker=key[1];b=books[ticker];want=expected[key]
     for side,index in [('yes',1),('no',2)]:
      got=[(p,b[side][p]) for p in sorted(b[side],reverse=True)[:10]]
      ref=[(fixed(p),fixed(q,2)) for p,q in want[index]]
      assert got==ref,(key,side,got,ref)
     found.add(key)
    if now>lastms:break
    if kind=='orderbook_snapshot':books[msg['market_ticker']]={s:{fixed(p):fixed(q,2) for p,q in msg[s+'_dollars_fp'] if fixed(q,2)>0} for s in ('yes','no')}
    elif kind=='orderbook_delta':
     b=books[msg['market_ticker']][msg['side']];px=fixed(msg['price_dollars']);qty=b.get(px,0)+fixed(msg['delta_fp'],2)
     assert qty>=0
     if qty==0:b.pop(px,None)
     else:b[px]=qty
  assert found==set(expected)
  receipts.append(dict(file=manifest[close]['file'],books=len(found)));print('RAW',len(receipts),len(targets),flush=True)
 (P/'raw_book_audit.json').write_text(json.dumps(dict(passed=True,books=sum(x['books'] for x in receipts),receipts=receipts),indent=2))
if __name__=='__main__':main()
