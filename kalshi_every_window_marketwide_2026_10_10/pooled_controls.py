"""Uniform broad baskets, avoiding outcome-selected market allocation."""
from core import *
def main():
    result={};ledgers={}
    for stem,kind in [('pregame','sports'),('daily','daily'),('lines','lines')]:
        file=P/(stem+'_candidates.json.gz')
        if not file.exists():continue
        cand=read(file);wi=P/(stem+'_index.json' if stem in ('daily','lines') else stem+'_windows.json');ws=read(wi)
        if stem=='pregame':policies=[f'pre5/{p}' for p in ['favorite','not_favorite','calibrated']]+['pre30/calibrated']
        elif stem=='daily':policies=['time0/'+p for p in ['favorite','not_favorite','calibrated']]
        else:policies=['yes','no','favorite','underdog']
        for policy in policies:
            for mode in ['base','delay60']:
                rr=[r for k,rows in cand.items() if k.endswith('/'+policy+'/'+mode) for r in rows]
                for part in ['all','october']:
                    choose=lambda d:part=='all' or d>='2026-10-01';rows=[r for r in rr if choose(r['date'])];den=len({w['owner'] for w in ws if choose(w['date'])})
                    for q in [1,2,5]:
                        for cost in [.01,.02]:record(result,ledgers,f'{kind}/{policy}/{mode}/{part}/q{q}/cost{round(cost*100)}',rows,den,q,cost)
    save(P/'pooled_results.json',result);save(P/'pooled_ledgers.json.gz',ledgers)
    for k,v in result.items():
        if '/base/all/q1/cost1' in k:print(k,{z:v[z] for z in ['windows','trades','net','drawdown','blocked']})
if __name__=='__main__':main()
