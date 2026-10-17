"""Independent ledger/depth/causality reconciliation; read-only inputs."""
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING
import collections,gzip,json,math,bisect,hashlib
import replay as r
P=Path(__file__).resolve().parent
def main():
 verified={};errors=[]
 for sample in ('dense','sparse'):
  all_ledgers={};by_ticker=collections.defaultdict(list)
  for suffix in ('','_stream'):
   p=P/(sample+suffix+'_ledgers.json.gz')
   if not p.exists():continue
   ll=json.load(gzip.open(p,'rt'));all_ledgers[sample+suffix]=ll
   for name,rows in ll.items():
    assert len({x['ticker'] for x in rows})==len(rows)
    for x in rows:by_ticker[x['ticker']].append((name,suffix,x))
  total=0;formulas=[];z_source_coverage=0
  for b in r.bundles(sample):
   m=b['market'];fs={int(f['t']):f for f in m['features']}
   for f in fs.values():
    computed=f['side']*(f['spot']/f['ref']-1)/(f['sigma']*math.sqrt(f['rem']))
    formulas.append(abs(computed-f['z']))
   for name,suffix,x in by_ticker[m['ticker']]:
    total+=1;delay=int(name.split('delay')[1].split('_')[0]);step=int(name.split('s_')[0]);stress=float(name.split('friction')[1]);rule=r.lane_for(x)
    assert x['quantity']==5 and 3-1e-9<=x['rem']<=10+1e-9
    assert x['entered']>=x['t']+delay-1e-8 and x['entered']<=x['t']+25+1e-8
    assert float(rule.floor)-1e-8<=x['ask']<float(rule.ceiling)-1e-8
    assert x['ask']-x['bid']<=.02000001
    assert x['z']>=rule.threshold(x['rem']) and x['fill_z']>=rule.threshold(x['rem'])
    assert x['won']==int(x['side']==m['result'])
    assert abs(x['net']-(5*x['won']-x['cost']-x['fee']-5*stress))<1e-7
    signal=dict(x,side='yes' if x['side']==1 else 'no',signal_ask=x['ask'],price_ranges=m['ranges'])
    assert abs(float(r.limit_price(signal)[1])-x['cap'])<1e-8
    book=b['books'][x['book_index']];assert abs(book[0]-x['entered'])<1e-8
    # Independently consume opposite bids; no depth above cap or rounding up.
    levels=sorted(book[2] if x['side']==1 else book[1],reverse=True);left=D(5);cost=D(0);fees=D(0)
    for bid,size in levels:
     px=1-D(str(bid));qty=D(str(size))
     if qty<=0:continue
     assert px<=D(str(x['cap']))+D('.00000001')
     n=min(qty,left);cost+=px*n;fees+=(D('.07')*px*n*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING);left-=n
     if left==0:break
    assert left==0 and cost/5>=rule.floor-D('.00000001')
    cashfee=(cost+fees).quantize(D('.0001'),rounding=ROUND_CEILING)-cost
    assert abs(float(cost)-x['cost'])<1e-7 and abs(float(cashfee)-x['fee'])<1e-7
    if suffix and step!=60:
     j=bisect.bisect_right([q[0] for q in b['books']],x['t'])-1
     assert j>=0 and x['t']-b['books'][j][0]<=(.5 if sample=='dense' else 2)+1e-8
   z_source_coverage+=len(b.get('cf',[]))
  verified[sample]=dict(ledger_rows_reconciled=total,minute_formula_count=len(formulas),max_formula_difference=max(formulas) if formulas else None,actual_cf_receipts=z_source_coverage)
  for prefix,ll in all_ledgers.items():
   summary=json.loads((P/(prefix+'_results.json')).read_text())['results']
   for name,rows in ll.items():
    got=r.metrics(rows)
    for key,value in got.items():assert value==summary[name][key],(prefix,name,key)
    assert summary[name]['risk']['max_reserved']<=25 and summary[name]['risk']['max_contracts']<=25
 assert all(x['response']['series']['fee_type']=='quadratic' and float(x['response']['series']['fee_multiplier'])==1 for x in json.loads((P/'fee_metadata.json').read_text()).values())
 before=json.loads((P/'preservation_before.json').read_text());after=json.loads((P/'preservation_after.json').read_text())
 same={k:before[k]==after[k] for k in ('source','flags','services')};assert all(same.values())
 r.save('validation.json',dict(passed=True,checks=verified,fee_multiplier=1,production_unchanged=same))
 print(json.dumps(verified,indent=2))
if __name__=='__main__':main()
