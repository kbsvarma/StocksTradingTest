"""Day-block uncertainty for incremental portfolio P&L; exploratory family=63.

Intervals condition on the already simulated daily portfolio path. They do not
turn reused history into new evidence or simulate an unseen market regime.
"""
import collections, json, pickle
import numpy as np
import study as s

def daily(rows,days):
    pp=collections.defaultdict(float)
    for r in rows:pp[r['day']]+=r['pnl']-.01*r['quantity']
    return np.array([pp[d] for d in days])

def intervals(delta,seed=20261003):
    n=len(delta)
    if n<2:return {'days':n,'inference':'Insufficient days'}
    rng=np.random.default_rng(seed)
    ix=rng.integers(0,n,size=(100000,n))
    means=delta[ix].mean(axis=1)
    starts=rng.integers(0,n,size=(100000,(n+1)//2))
    ix2=np.stack((starts,(starts+1)%n),axis=-1).reshape(100000,-1)[:,:n]
    means2=delta[ix2].mean(axis=1)
    return dict(days=n,mean_incremental_per_day=float(delta.mean()),
        daily_bootstrap_95=[float(x) for x in np.quantile(means,[.025,.975])],
        two_day_block_95=[float(x) for x in np.quantile(means2,[.025,.975])],
        lower_family63_per_day=float(np.quantile(means,.05/63)),
        extra_loss_at_five_contracts_wipes_net=bool(delta.sum()<5),
        incremental_without_best_three_days=float(delta.sum()-sum(sorted(delta,reverse=True)[:3])))

if __name__=='__main__':
    markets=pickle.loads((s.P/'features.pickle').read_bytes())
    selected=['gaussian_edge8:ETH','late_z3:BOTH','official_average_edge5:BTC','official_z2:BTC',
        'late_z2:BOTH','adaptive_edge5:BOTH','rich_edge3:BOTH']
    out={}
    for scenario in ['quote','book','book_delay10','quote_delay60']:
        data=pickle.loads((s.P/(scenario+'_trades.pickle')).read_bytes());out[scenario]={}
        for co,groups in data.items():
            days=sorted({s.day(m['close']) for m in markets if s.cohort(m['close'])==co})
            base=daily(groups['baseline'],days);out[scenario][co]={}
            for key in selected:
                if key in groups:out[scenario][co][key]=intervals(daily(groups[key],days)-base)
    s.save('uncertainty.json',out)
