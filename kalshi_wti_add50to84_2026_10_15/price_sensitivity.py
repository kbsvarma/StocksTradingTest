"""Conditional witness-price arithmetic, explicitly not an order-book replay.

The main comparable model always spends the cap. A limit cap does not mean an
actual buyer necessarily pays that cap. This diagnostic reprices the SAME modeled
additions at the subsequent public transaction price. That transaction consumed
liquidity and is NOT proof that the extra five could be bought at this price.
"""
import gzip, json
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

P=Path(__file__).resolve().parent
ledgers=json.load(gzip.open(P/'ledgers.json.gz'))
results={};details={};checks=0
for scenario,caps in ledgers.items():
    results[scenario]={};details[scenario]={}
    for cap,rows in caps.items():
        gains=losses=fees=D(0);xs=[]
        for r in rows:
            if not r['added']:continue
            price=D(str(r['execution_print_price']))
            assert 0<price<=D(cap)/100 and r['execution_print_qty']>=5
            formula=(D('.07')*5*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)
            debit=(5*price+formula).quantize(D('.0001'),rounding=ROUND_CEILING)
            pnl=5*int(r['won'])-debit
            if r['won']:gains+=pnl
            else:losses-=pnl
            fees+=debit-5*price
            xs.append(dict(ticker=r['ticker'],won=r['won'],price=float(price),
                           cost=float(5*price),fee=float(debit-5*price),extra_net=float(pnl)))
            checks+=1
        n=len(xs);extra=gains-losses
        results[scenario][cap]=dict(adds=n,extra_winner_profit=float(gains),
             extra_losses=float(losses),extra_net=float(extra),fees=float(fees),
             total_net=float(D('15.3050')+extra),
             extra_net_friction1c=float(extra-D('.05')*n),
             total_net_friction1c=float(D('15.3050')+extra-D('.05')*n),
             assumption='Same modeled additions priced at another trader\'s execution price; not verified available depth')
        details[scenario][cap]=xs
(P/'witness_price_sensitivity.json').write_text(json.dumps(results,indent=2)+'\n')
with gzip.open(P/'witness_price_ledgers.json.gz','wt') as h:json.dump(details,h)
for s,caps in results.items():
    best=max(caps,key=lambda c:caps[c]['extra_net'])
    print(s,'witness-price best',best,caps[best]['extra_net'])
    for cap in ['50','56','63','65','70','74','79','84']:
        r=caps[cap];print(cap,'gain',r['extra_winner_profit'],'loss',r['extra_losses'],
                          'extra',r['extra_net'],'total',r['total_net'])
print('Decimal witness-price cases',checks)
