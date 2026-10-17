"""Independently reconstruct selected exit books directly from raw receipts."""
from pathlib import Path
import json,pickle,gzip,collections
P=Path(__file__).resolve().parent;R=P.parent
def fixed(s,n=4):
 return round(float(s)*10**n)
def main():
 raw=pickle.load(gzip.open(P/'inputs.pkl.gz'));led=json.load(gzip.open(P/'ledgers.json.gz'))['actual'];targets=collections.defaultdict(dict)
 for key in ['i1_d1_g0_p0','i10_d1_g0_p0','i30_d1_g0_p0','i60_d1_g0_p0','i1_d2_g0_p0','i10_d2_g0_p0','i30_d2_g0_p0','i60_d2_g0_p0']:
  for r in led[key]:
   if not r['exit']:continue
   b=raw['bundles'][r['ticker']]
   for field in ('book_before','book_after'):
    ms=round(r[field]*1000);row=next(x for x in b['books'] if x[0]==ms);targets[int(r['close'])][(ms,r['ticker'])]=row
 mf={int(m['file'].split('_')[1]):m for m in json.loads((R/'kalshi_scalp_feasibility_2026_10_14/capture_manifest.json').read_text()) if m['accepted']};receipts=[]
 for close,expected in targets.items():
  books={};found=set();lastms=max(t for t,ticker in expected)
  with gzip.open(R/'kalshi_btc_eth_quant_2026_10_02/forward_state/data'/mf[close]['file'],'rb') as f:
   for line in f:
    r=json.loads(line);now=r['client_received_at_ms'];m=r.get('msg',{});kind=r['type']
    for key in [(now,ticker) for ticker in books if (now,ticker) in expected and (now,ticker) not in found]:
     ticker=key[1];b=books[ticker];row=expected[key]
     for side,index in [('yes',3),('no',4)]:
      levels=[[p,b[side][p]] for p in sorted(b[side],reverse=True)[:10]]
      assert levels==row[index],(ticker,now,side,levels,row[index])
     found.add(key)
    if now>lastms:break
    if kind=='orderbook_snapshot':books[m['market_ticker']]={s:{fixed(p):fixed(q,2) for p,q in m[s+'_dollars_fp'] if fixed(q,2)>0} for s in ('yes','no')}
    elif kind=='orderbook_delta':
     b=books[m['market_ticker']][m['side']];p=fixed(m['price_dollars']);q=b.get(p,0)+fixed(m['delta_fp'],2)
     if q<=0:b.pop(p,None)
     else:b[p]=q
  assert found==set(expected),(close,len(found),len(expected))
  receipts.append(dict(file=mf[close]['file'],verified_books=len(found)));print('verified',close,len(found),flush=True)
 (P/'raw_book_audit.json').write_text(json.dumps(dict(passed=True,receipts=receipts,total_books=sum(x['verified_books'] for x in receipts)),indent=2)+'\n')
if __name__=='__main__':main()
