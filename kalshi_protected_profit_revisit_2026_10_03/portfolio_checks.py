"""Continuous cash/risk sensitivity; no resetting cash at block boundaries."""
import json
import numpy as np
import study as s
P=s.P;x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());cc=json.loads((P/'configurations.json').read_text());out={}
for ids in ((2,),(2462,),(1771,),(3904,),(2,2462),(1771,3904)):
    for n in (1,5):
        rows=[]
        for cid in ids:rows.extend(s.trades(cc[cid],x,mm,quantity=n))
        accepted,blocked=s.portfolio(rows);den=sum(m['asset'] in {cc[cid]['asset'] for cid in ids} for m in mm)
        key='_'.join(map(str,ids))+f'_n{n}';out[key]=dict(initial_cash=100,daily_loss_budget_including_open_cost=10,minimum_remaining_cash=5,daily_timezone='UTC',metrics=s.metrics(accepted,den),rejected=blocked,ending_cash=100+sum(r['pnl']-.0025*r['quantity']*r['legs'] for r in accepted));print(key,len(accepted),out[key]['metrics']['net'],blocked)
s.save('continuous_portfolio.json',out)
