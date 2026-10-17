"""Reprice historical recorded-book opportunities at the requested five contracts."""
import json
from pathlib import Path
import pickle
import sys

P=Path(__file__).resolve().parent
R=P.parent/'kalshi_parallel_design_2026_10_02'
sys.path.insert(0,str(R))
from research import load_books,replay,RULES,ts,metrics
from portfolio import portfolio

def main():
    markets=pickle.loads((R/'features.pickle').read_bytes())
    markets=[m for m in markets if ts('2026-08-13T00:00:00Z')<=m['close']<ts('2026-09-12T00:00:00Z')]
    books=load_books();rule=next(r for r in RULES if r.name=='premium_z3')
    result={}
    for n in (1,5):
        rows,counts=replay(markets,None,books,quantity=n,all_signals=True,rules=[rule])
        # Both sizes use the existing shared account ceilings, with nested lane limits.
        contracts,risk,daily=(4,4,5) if n==1 else (20,19,10)
        selected,blocked=portfolio(rows,['premium_z3'],contracts,risk,daily)
        result[str(n)]=dict(metrics(selected),blocked=blocked,qualifying_signals=counts,
                           depth_supported_opportunities=len(rows.get('premium_z3',[])))
    out=dict(period='August 13–September 11 UTC, continuous 30-day replay',
             scenario='Recorded books, sufficient full quantity inside unchanged cap; modeled fees plus 1c per contract; premium standalone under current account ceilings',
             by_contract_size=result,
             limitations='Historical displayed depth is not guaranteed live IOC execution; this comparison is not the joint portfolio replay.')
    (P/'five_contract_depth.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))

if __name__=='__main__':main()
