"""Explicitly exploratory sign inversion and official-site observation bounds."""
from conditional import *

def inverse():
    original=read(P/'conditional_intents.json.gz').get('persistence/blend0.5',[])
    # Fresh quote reads for the chosen strike, without selecting on its outcome.
    _,events=load();intents=[]
    for r in original:
        cs=events[r['event']]['cm'][r['ticker']];q=quote(cs.get(r['t']))
        side='no' if r['side']=='yes' else 'yes';ask=sq(q,side)[1]
        if not .05<=ask<.95:continue
        x=dict(r,side=side,signal_ask=ask,cap=min(.95,ask+.01),won=not r['won'],model_p=1-r['model_p'])
        x['future']={mode:quote(cs.get(r['t']+60),phase) for mode,phase in [('base','open'),('delay60','close')]}
        intents.append(x)
    results={};ledgers={}
    for mode,friction in [('base',.01),('base',.02),('delay60',.01),('delay60',.02),('cap',.01)]:
        candidates=[];skips=Counter()
        for r in intents:
            q=r['future']['base' if mode=='cap' else mode]
            if not q:skips['missing_quote']+=1;continue
            entry=max(r['signal_ask'],sq(q,r['side'])[1])
            if entry>r['cap']+1e-8:skips['cap']+=1;continue
            if mode=='cap':entry=r['cap']
            candidates.append(dict({k:v for k,v in r.items() if k!='future'},entry=entry,entered=r['t']+(60 if mode=='delay60' else 10),fee=fee(entry,1)))
        ledger,risk=portfolio(candidates,friction);key=f'{mode}/cost{int(100*friction)}c';ledgers[key]=ledger
        results[key]={'all':describe(ledger),'earlier':describe([r for r in ledger if r['t']<SPLIT],START,SPLIT),
            'later':describe([r for r in ledger if r['t']>=SPLIT],SPLIT,END),'cities':{c:metrics([r for r in ledger if r['asset']==c]) for c in CITIES},
            'risk':risk,'execution_skips':dict(skips),'intents':len(intents)}
        print('INVERSE',key,len(ledger),results[key]['all']['net'],flush=True)
    save(P/'inverse_results.json',results);save(P/'inverse_ledgers.json.gz',ledgers)

def official():
    sys.path.insert(0,str(P.parent/'kalshi_cross_market_edges_2026_10_11'))
    from weather_bounds import STATIONS
    index=[g for g in read(P.parent/'kalshi_every_window_marketwide_2026_10_10/daily_index.json') if g['asset'] in STATIONS]
    obs=defaultdict(dict)
    for f in sorted((P/'twc_weeks').glob('*.json.gz')):
        for station in read(f)['response']['stations']:
            for r in station.get('observations',[]):
                if r.get('tempF') is not None:
                    t=ts(r['reportTimeUTC']);key=(station['icaoId'],r['localDate'])
                    if t in obs[key]:assert obs[key][t]==float(r['tempF'])
                    if r['localHour']>=6:obs[key][t]=float(r['tempF'])
    quality=[];intents=defaultdict(list);counts=Counter()
    for g in index:
        station,zone=STATIONS[g['asset']];observed=obs.get(('K'+station,g['date']),{})
        final={float(m['expiration_value']) for m in g['markets'] if m.get('expiration_value') not in ('',None)}
        quality.append(dict(asset=g['asset'],date=g['date'],station='K'+station,n=len(observed),
            observed_max=max(observed.values()) if observed else None,final=sorted(final),
            exceeds_final=max(observed.values())-next(iter(final)) if observed and len(final)==1 else None))
        if not observed:counts['missing_station_day']+=1;continue
        path=P.parent/'kalshi_cross_market_edges_2026_10_11/weather_intraday'/(g['event']+'.json.gz')
        if not path.exists():counts['missing_quote_file']+=1;continue
        cm={k:{c['end_period_ts']:c for c in v} for k,v in read(path).items()}
        date_=datetime.fromisoformat(g['date']).replace(tzinfo=ZoneInfo(zone))
        for lag,margin in [(30,2),(15,2),(60,2),(30,1),(30,3)]:
            name=f'lag{lag}/margin{margin}'
            for hour in range(10,23):
                t=int(date_.replace(hour=hour).timestamp());available=[(s,v) for s,v in observed.items() if s<=t-lag*60]
                if not available:counts[name+'/no_source']+=1;continue
                high=max(v for s,v in available);choices=[]
                for m in g['markets']:
                    if m.get('strike_type') not in ('between','less','less_or_equal') or m.get('cap_strike') is None:continue
                    if not high>float(m['cap_strike'])+margin:continue
                    counts[name+'/bracket_ruled_out']+=1
                    cs=cm.get(m['ticker'],{});q=quote(cs.get(t))
                    if q is None:counts[name+'/missing_quote']+=1;continue
                    ask=1-q[0]
                    if not .1<=ask<=.97:counts[name+'/price']+=1;continue
                    if q[1]-q[0]>.05000001:counts[name+'/spread']+=1;continue
                    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:counts[name+'/volume']+=1;continue
                    choices.append(dict(asset=g['asset'],event=g['event'],ticker=m['ticker'],t=t,close=ts(m['close_time']),settled=ts(m['settlement_ts']),
                        side='no',signal_ask=ask,cap=min(.97,ask+.01),observed_max=high,source_last=max(s for s,v in available),lag=lag,margin=margin,
                        model_p=1.,won=m['result']=='no',mult=g['mult'],rules_primary=m['rules_primary'],
                        future={mode:quote(cs.get(t+60),phase) for mode,phase in [('base','open'),('delay60','close')]}))
                if choices:intents[name].append(min(choices,key=lambda r:(r['signal_ask'],r['ticker'])))
    results={};ledgers={}
    # Empty strategies retain explicit zero rows instead of disappearing.
    for name in ['lag30/margin2','lag15/margin2','lag60/margin2','lag30/margin1','lag30/margin3']:
        rr=intents[name]
        for mode,friction in [('base',.01),('base',.02),('delay60',.01),('delay60',.02)]:
            candidates=[];skips=Counter()
            for r in rr:
                if not 'Weather Company' in r['rules_primary']:skips['other_settlement_source']+=1;continue
                q=r['future'][mode]
                if q is None:skips['missing_next_quote']+=1;continue
                entry=max(r['signal_ask'],1-q[0]);entered=r['t']+(60 if mode=='delay60' else 10)
                if entry>r['cap']+1e-8:skips['cap']+=1;continue
                if entered>=r['close']:skips['closed']+=1;continue
                candidates.append(dict({k:v for k,v in r.items() if k!='future'},entry=entry,entered=entered,fee=fee(entry,r['mult'])))
            key=f'{name}/{mode}/cost{int(friction*100)}c'
            # Evaluate fixed Sep12–Oct9 rather than giving this variant a new period.
            lo=START;hi=ts('2026-10-10T04:00:00Z')
            ledger,risk=portfolio([r for r in candidates if lo<=r['t']<hi],friction)
            ledgers[key]=ledger;results[key]={'all':describe(ledger,lo,hi),'risk':risk,'execution_skips':dict(skips),'intents':sum(lo<=r['t']<hi for r in rr),
                'cities':{c:metrics([r for r in ledger if r['asset']==c]) for c in STATIONS}}
            print('OFFICIAL',key,len(ledger),results[key]['all']['net'],flush=True)
    save(P/'official_results.json',results);save(P/'official_ledgers.json.gz',ledgers);save(P/'official_intents.json.gz',dict(intents));save(P/'official_source_audit.json',quality);save(P/'official_skip_counts.json',dict(counts))

if __name__=='__main__':inverse();official()
