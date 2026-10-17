"""Compact evidence and daily-cluster uncertainty; no selection of entry rules."""
from collections import Counter
import gzip
import json
import pathlib
import numpy as np
import replay as r

def main():
    result=json.loads((r.P/'results.json').read_text())
    coverage=json.loads((r.P/'coverage.json').read_text())
    inventory={x['series']:x for x in json.loads((r.P/'inventory.json').read_text())}
    with gzip.open(r.P/'trade_ledgers.json.gz','rt') as f:ledgers=json.load(f)
    rows=[]
    rng=np.random.default_rng(20261007)
    for asset in r.ASSETS:
        periods=result['opening_plus1c']['periods']
        row=dict(asset=asset,pooled=periods['pooled'][asset],prior=periods['prior_period'][asset],
                 fresh=periods['fresh_oct3_7'][asset],coverage=coverage[asset],
                 inventory=inventory['KX'+asset+'15M'])
        row['stress']={k:{p:result[k]['periods'][p][asset]['net'] for p,_,_ in r.windows()}
                       for k in result if k!='opening_plus1c'}
        trades=ledgers['opening_plus1c/pooled/'+asset]
        if trades:
            row['mean_entry']=sum(x['entry'] for x in trades)/len(trades)
            row['ex_post_break_even_win_rate']=sum(x['entry']+x['fee']/5+.01 for x in trades)/len(trades)
            daily=np.array(list(row['pooled']['daily'].values()))
            samples=rng.choice(daily,size=(10000,len(daily)),replace=True).sum(axis=1)
            row['exploratory_day_bootstrap_net_95pct']=list(np.quantile(samples,[.025,.975]))
        rows.append(row)
    r.save('summary.json',rows)
    for x in rows:
        a,p,f=x['asset'],x['pooled'],x['fresh']
        print(a,'ALL',p['trades'],p['wins'],p['losses'],p['net'],'DD',p['settled_drawdown'],
              'NEW',f['trades'],f['wins'],f['losses'],f['net'],
              '2c',x['stress']['opening_plus2c']['pooled'],'cap',x['stress']['cap_cost_plus1c']['pooled'],
              'delay',x['stress']['delay60_plus1c']['pooled'],flush=True)
    observations=r.P/'live_probe/observations.jsonl'
    if observations.exists():
        obs=list(map(json.loads,observations.read_text().splitlines()))
        checks=Counter(v['status'] for x in obs for v in x['lanes'].values())
        opportunities=[x for x in obs if any(v['status']=='five_contract_opportunity' for v in x['lanes'].values())]
        r.save('live_probe_summary.json',dict(observations=len(obs),
            errors=[x for x in obs if x.get('error')],statuses=dict(checks),
            five_contract_two_sided_books=sum(all(v.get('count')==5 for v in x.get('five_contract_depth',{}).values())
                and len(x.get('five_contract_depth',{}))==2 for x in obs),
            within35s=sum(x.get('book_age_from_decision',1e9)<=35 for x in obs),opportunities=opportunities,
            not_live_orders=True,not_fill_evidence=True))

if __name__=='__main__':main()
