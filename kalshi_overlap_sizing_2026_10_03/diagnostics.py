"""Descriptive follow-up; no strategy selection, fitting or production changes."""
from replay import *

def main():
    allrows=pickle.loads((P/'trades.pickle').read_bytes());out={}
    for source,periods in allrows.items():
        out[source]={}
        for period,policies in periods.items():
            rr=policies['parallel5'];first={r['ticker']:r for r in rr if not r['second']};adds=[r for r in rr if r['second']]
            routes=collections.Counter(f"z{2+first[r['ticker']]['lane']}->z{2+r['lane']}" for r in adds)
            n=len(adds);cost=sum(r['quantity']*r['entry']+r['fee']+.01*r['quantity'] for r in adds)
            out[source][period]=dict(addons=n,route_counts=dict(routes),assets=dict(collections.Counter(r['asset'] for r in adds)),
                avg_entry=sum(r['entry'] for r in adds)/n if n else None,
                fee_stress_breakeven_win_rate=cost/(5*n) if n else None,
                nominal_one_sided_95_loss_upper_bound=(1-.05**(1/n)) if n and not any(not r['won'] for r in adds) else None,
                loss_flip_changes_fixed_portfolio_net_by_dollars=5,
                note='Binomial bound assumes independent outcomes; crypto clustering and prior history reuse weaken inference. A win-to-loss flip of one five-contract leg changes its return by exactly $5 before future portfolio effects.')
    # Broader availability without the portfolio acceptance filter. First entry per lane
    # at observed executable opportunities, second only while first is open and same side.
    markets=pickle.loads((s.P/'features.pickle').read_bytes());books=s.old.load_books();broader={}
    for source in ('quote','books25','books25_delay10'):
        ev,_,_,_=events(source,markets,books if source!='quote' else None);first={};seconds=[];used=set()
        for e in ev:
            if e['ticker'] not in first:first[e['ticker']]=e;continue
            f=first[e['ticker']]
            if e['ticker'] in used or e['lane']==f['lane'] or e['side']!=f['side'] or e['entered']>=f['settled']:continue
            fill=e['second_fill'] if e['entered']==f['entered'] else e['fills'][5]
            if fill is None:continue
            seconds.append(dict(e,entry=fill[0],fee=fill[1]));used.add(e['ticker'])
        broader[source]=dict(markets=len(seconds),wins=sum(e['won'] for e in seconds),losses=sum(not e['won'] for e in seconds),
            losing_tickers=[e['ticker'] for e in seconds if not e['won']],
            note='Ignores portfolio limits and cash; availability diagnostic only, not a deployable strategy result.')
    save('diagnostics.json',dict(accepted=out,broader_two_lane_markets=broader))
    print(json.dumps(dict(accepted=out['quote']['all'],broader=broader),indent=2))

if __name__=='__main__':main()
