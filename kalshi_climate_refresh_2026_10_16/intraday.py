"""Rain observation event-time extension; public history only."""
from replay import *
from collect import get,BASE
import time

def collect():
    groups=defaultdict(list)
    for r in json.load(open(P/'rain_source_audit.json')):
        first=r['first_positive']['0.01']
        if first is not None:groups[r['date']].append(r)
    folder=P/'rain_intraday';folder.mkdir(exist_ok=True)
    n=0
    for date,rs in sorted(groups.items()):
        for i in range(0,len(rs),4):
            batch=rs[i:i+4];file=folder/f'{date}_{i}.json.gz'
            if file.exists():continue
            points=[v for r in batch for v in r['first_positive'].values() if v is not None]
            lo=min(points)-600;hi=max(points)+91*60
            d=get(BASE+'/markets/candlesticks',dict(market_tickers=','.join(r['ticker'] for r in batch),start_ts=lo,end_ts=hi,period_interval=1))
            with gzip.open(file,'wt') as f:json.dump(d,f)
            n+=1
            if n%10==0:print('RAIN_HISTORY',n,flush=True)
            time.sleep(1.)
    print('RAIN_HISTORY_DONE',n,flush=True)

def run():
    source=json.load(open(P/'rain_source_audit.json'));index={g['owner']:g for g in json.load(open(OLD/'daily_index.json')) if g['asset']=='KXRAIN'};quotes={}
    for path in sorted((P/'rain_intraday').glob('*.gz')):
        d=json.load(gzip.open(path,'rt'))['response']
        for r in d.get('markets',[]):quotes[r['market_ticker']]={c['end_period_ts']:c for c in r['candlesticks']}
    variants=[(.02,30),(.01,30),(.05,30),(.02,5),(.02,15),(.02,60)]
    intents=defaultdict(list);skips=defaultdict(Counter);coverage=[]
    for r in source:
        g=index[r['ticker']];m=g['markets'][0];cs=quotes.get(r['ticker'],{})
        if r['first_positive']['0.01'] is not None:coverage.append(dict(ticker=r['ticker'],bars=len(cs),valid_quotes=sum(qte(c) is not None for c in cs.values())))
        for level,lag in variants:
            name=f'p{level:.2f}_lag{lag}';first=r['first_positive'][str(level)]
            if first is None:continue
            start=math.ceil((first+lag*60)/60)*60
            for mode,phase in [('next_open','open'),('delay60','close')]:
                key=name+'/'+mode
                for t in range(start,start+30*60+1,60):
                    if not ts(m['open_time'])<=t<ts(m['close_time']):skips[key]['not_open']+=1;continue
                    q=qte(cs.get(t))
                    if q is None:skips[key]['missing_signal_quote']+=1;continue
                    if not .01<=q[1]<=.97:skips[key]['price']+=1;continue
                    if q[1]-q[0]>.050001:skips[key]['spread']+=1;continue
                    volume=sum(float(c.get('volume_fp') or 0) for s,c in cs.items() if t-600<s<=t)
                    if volume<5:skips[key]['volume']+=1;continue
                    q2=qte(cs.get(t+60),phase=phase)
                    if not q2:skips[key]['missing_execution_quote']+=1;continue
                    entry=max(q[1],q2[1]);cap=min(.97,q[1]+.01)
                    if entry>cap+1e-9:skips[key]['execution_cap']+=1;continue
                    entered=t+(60 if mode=='delay60' else 3)
                    if entered>=min(ts(m['close_time']),ts(m['settlement_ts'])):skips[key]['closed']+=1;continue
                    intents[key].append(dict(asset=r['station'],owner=r['ticker'],ticker=r['ticker'],date=r['date'],t=t,entered=entered,entry=entry,signal_ask=q[1],next_ask=q2[1],cap=cap,settled=ts(m['settlement_ts']),payout=float(m['result']=='yes'),source_t=first,volume10=volume));break
    results={};ledgers={}
    for level,lag in variants:
        for mode in ['next_open','delay60']:
            name=f'p{level:.2f}_lag{lag}/{mode}'
            for friction in [.01,.02]:
                for enforce in [True,False]:
                    key=name+f'/cost{friction}/'+('budgeted' if enforce else 'all_opportunities');led,blocked=simulate(intents[name],friction,enforce);ledgers[key]=led
                    results[key]=dict(**stats(led),trades_per_calendar_day=len(led)/56,blocked=blocked,
                        cities={c:stats([r for r in led if r['asset']==c]) for c in sorted(set(r['asset'] for r in led))},
                        earlier=stats([r for r in led if r['date']<'2026-10-01']),later=stats([r for r in led if r['date']>='2026-10-01']))
                    if friction==.01 and enforce:print(key,{a:results[key][a] for a in ['trades','wins','losses','net','drawdown']},flush=True)
    save('rain_intraday_results.json',results);save('rain_intraday_ledgers.json',ledgers);save('rain_intraday_skips.json',skips);save('rain_intraday_coverage.json',coverage)

if __name__=='__main__':collect();run()
