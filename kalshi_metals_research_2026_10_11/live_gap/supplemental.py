"""Final-book slippage and the one unrecoverable Copper input minute."""
import json
import pickle
import sys
from pathlib import Path
from decimal import Decimal as D
P=Path(__file__).resolve().parent.parent;OUT=P/'live_gap'
sys.path.insert(0,str(P))
import study as s

def price(levels,cap):
    qty=D(0);cost=D(0)
    for p,n in sorted((1-D(p),D(n)) for p,n in levels):
        if n<=0:continue
        if p>D(str(cap)):break
        take=min(5-qty,n);qty+=take;cost+=take*p
        if qty==5:break
    assert qty==5
    return cost/qty

actual=json.loads((P/'actual_metal_orders.json').read_text());rows=[]
for r in actual:
    sg=r['signal'];b=price(sg['execution_book_levels'],sg['execution_cap'])
    rows.append(dict(ticker=r['ticker'],book_average=str(b),actual_average=str(D(r['cost'])/5),
        slippage_dollars=str(D(r['cost'])-5*b),
        cost_minus_signal_ask=str(D(r['cost'])-5*D(str(sg['signal_ask'])))))
(OUT/'fill_slippage.json').write_text(json.dumps(rows,indent=2)+'\n')
print('FINALBOOK_SLIPPAGE',sum(D(r['slippage_dollars']) for r in rows),
      'MAX_PER_TRADE',max(abs(D(r['slippage_dollars'])) for r in rows))
print('VS_SIGNAL',sum(D(r['cost_minus_signal_ask']) for r in rows))
bars=pickle.loads((P/'bars.pickle').read_bytes())['COPPER']
mm=pickle.loads((P/'features.pickle').read_bytes())
m=next(x for x in mm if x['ticker']=='KXCOPPER15M-26OCT081830-30');T=1791498300
missing=[k for k in range(T-3660,T,60) if k not in bars]
result=dict(ticker=m['ticker'],decision_ts=T,missing_minute_starts=missing,
    reference_ts=m['start']-60,reference_value=bars.get(m['start']-60),official_result=m['result'],
    price_floor=.8,best_possible_win_after_model_fee=5*(1-.8)-s.fee(.8),
    note='Historical complete input absent too; no eligible signal or executable book established.')
(OUT/'missing_source_evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print('MISSING_SOURCE',json.dumps(result))
losses=json.loads((P/'loss_mechanism_audit.json').read_text())
(OUT/'losing_underlying_paths.json').write_text(json.dumps(losses,indent=2)+'\n')
print('LOSS_PATHS',json.dumps([{k:r[k] for k in ('ticker','ref','spot','path')} for r in losses]))
