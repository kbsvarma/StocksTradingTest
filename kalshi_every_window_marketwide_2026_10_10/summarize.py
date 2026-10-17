"""Honest frequency frontier and selected shared-bankroll replay."""
from core import *
import numpy as np

def frequency(rows,windows):
    dates=[w['date'] for w in windows]
    if not dates:return {}
    lo,hi=min(dates),max(dates);dt=datetime.fromisoformat(lo);end=datetime.fromisoformat(hi);counts=Counter(day(r['entered']) for r in rows)
    # Gas positions enter previous day, so include that entry calendar boundary.
    if rows:dt=min(dt,datetime.fromisoformat(min(counts)))
    days=[]
    while dt<=end:days.append(dt.date().isoformat());dt+=timedelta(days=1)
    nd=len(days);wd=set(dates)
    return {'first_window':lo,'last_window':hi,'calendar_days':nd,'calendar_trades_per_day':len(rows)/nd,'entry_days':sum(counts[d]>0 for d in days),'zero_entry_days':sum(counts[d]==0 for d in days),'multi_entry_days':sum(counts[d]>=2 for d in days),'window_days':len(wd),'trades_per_window_day':len(rows)/len(wd)}

def uncertainty(rows,windows):
    # Circular 7-calendar-day moving block bootstrap, descriptive only:
    # selected rules / reused outcomes mean these are not selection-adjusted CIs.
    f=frequency(rows,windows)
    if not f:return {}
    lo=datetime.fromisoformat(f['first_window']);n=f['calendar_days'];daily=defaultdict(float)
    for r in rows:daily[day(r['entered'])]+=r['net']
    if rows:lo=min(lo,datetime.fromisoformat(min(daily)))
    v=np.array([daily[(lo+timedelta(days=i)).date().isoformat()] for i in range(n)]);rng=np.random.default_rng(10102026);means=[]
    for _ in range(3000):
        starts=rng.integers(0,n,size=math.ceil(n/7));ix=((starts[:,None]+np.arange(7)[None,:])%n).ravel()[:n];means.append(v[ix].sum())
    return {'descriptive_7day_block_total_net_95pct':np.quantile(means,[.025,.975]).tolist(),'without_best_day':round(sum(v)-max(v,default=0),5),'not_selection_adjusted':True}

def main():
    inventory=defaultdict(lambda:{'open_events':0,'open_markets':0,'volume24h':0});ev={e['event_ticker']:e for e in map(json.loads,(P/'data/events.jsonl').read_text().splitlines())}
    for e in ev.values():
        a=inventory[e.get('category','unknown')];a['open_events']+=1;a['open_markets']+=len(e.get('markets',[]));a['volume24h']+=sum(float(m.get('volume_24h_fp') or m.get('volume_24h') or 0) for m in e.get('markets',[]))
    save(P/'inventory_summary.json',inventory)
    summaries={};appendix=[]
    datasets=[(P,s) for s in ['segment','hourly_fifteen_weather','pregame','followup','daily','lines']]+[(P/'daily_extension','daily'),(P/'wnba_extension','pregame')]
    for root,stem in datasets:
        if not (root/(stem+'_results.json')).exists():continue
        result=read(root/(stem+'_results.json'));led=read(root/(stem+'_ledgers.json.gz'))
        windows=read(root/(stem+'_index.json' if stem in ('daily','lines') else stem+'_windows.json'))
        for key,v in result.items():
            if '/base/all/q1' not in key or ('cost' in key and not key.endswith('cost1')):continue
            asset=key.split('/')[0];ws=[w for w in windows if w['asset']==asset or (stem=='followup' and ((asset=='NFL' and w['asset']=='KXNFLGAME') or (asset=='CS2' and w['asset']=='KXCS2GAME')))]
            sid=str(root.relative_to(P))+'/'+stem+'/'+key;rows=led[key]
            compact={a:v[a] for a in ['windows','trades','wins','losses','scalar','net','drawdown','participation','blocked','without_best3']};compact.update(frequency(rows,ws));summaries[sid]=compact
            appendix.append(f"{sid}\t{v['trades']}/{v['windows']}\t{v['wins']}W/{v['losses']}L/{v['scalar']}scalar\t{v['net']:+.5f}\tDD{v['drawdown']:.5f}")
    save(P/'frequency_frontier.json',summaries);(P/'APPENDIX.txt').write_text('All baseline one-contract profiles; additional stresses/sizes are in results JSON. These are overlapping alternatives, not additive trades.\n'+'\n'.join(appendix)+'\n')
    # Frozen finalist rules; deliberately report standalone and combined to
    # expose seasonality and dependence. Do not pool independent ledger P&Ls.
    dc=read(P/'daily_extension/daily_candidates.json.gz');pc=read(P/'wnba_extension/pregame_candidates.json.gz');di=read(P/'daily_extension/daily_index.json');pi=read(P/'wnba_extension/pregame_windows.json')
    components={'US_gas':(dc,'KXAAAGASD/time1/favorite',[w for w in di if w['asset']=='KXAAAGASD']),'NY_high':(dc,'KXHIGHNY/time0/favorite',[w for w in di if w['asset']=='KXHIGHNY']),'WNBA':(pc,'KXWNBAGAME/pre30/calibrated',pi)}
    baskets={'US_gas':['US_gas'],'NY_high':['NY_high'],'WNBA':['WNBA'],'Daily_pair':['US_gas','NY_high'],'Pair_plus_WNBA':['US_gas','NY_high','WNBA']};result={};ledgers={};diagnostics={}
    for name,parts in baskets.items():
        ws=[w for c in parts for w in components[c][2]]
        for mode in ['base','delay60']:
            rows=[r for c in parts for r in components[c][0].get(components[c][1]+'/'+mode,[])]
            for period in ['all','older','recent','october']:
                choose=lambda d:period=='all' or (d<'2026-08-15' if period=='older' else d>='2026-08-15' if period=='recent' else d>='2026-10-01')
                ww=[w for w in ws if choose(w['date'])];rr=[r for r in rows if choose(r['date'])]
                for q in [1,2,5]:
                    for cost in [.01,.02]:
                        key=f'{name}/{mode}/{period}/q{q}/cost{round(cost*100)}';record(result,ledgers,key,rr,len({w['owner'] for w in ww}),q,cost);result[key].update(frequency(ledgers[key],ww))
                        if mode=='base' and q==1 and cost==.01:
                            diagnostics[key]={**uncertainty(ledgers[key],ww),'monthly':{mo:stats([r for r in ledgers[key] if r['date'][:7]==mo]) for mo in sorted({r['date'][:7] for r in ledgers[key]})}}
    save(P/'joint_results.json',result);save(P/'joint_ledgers.json.gz',ledgers);save(P/'joint_diagnostics.json',diagnostics)
    for k,v in result.items():
        if '/base/' in k and k.endswith('/q1/cost1'):print(k,{z:v.get(z) for z in ['windows','trades','wins','losses','net','drawdown','participation','calendar_trades_per_day']})
if __name__=='__main__':main()
