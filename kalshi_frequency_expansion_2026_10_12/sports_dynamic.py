"""New intramatch follow/fade/exit mechanisms on frozen complete game tapes."""
import argparse
import bisect
import collections as C
import math
import pickle
from core import *
OLD=ROOT/'kalshi_every_window_marketwide_2026_10_10'
N=ROOT/'kalshi_noncrypto_consistency_2026_10_10'
B=ROOT/'kalshi_broad_review_2026_10_10/replays'
T=ROOT/'kalshi_cross_market_edges_2026_10_11/tennis_older_inputs'
PERIODS={'development':(ts('2026-08-15T04:00:00Z'),ts('2026-09-12T04:00:00Z')),
    'selection':(ts('2026-09-12T04:00:00Z'),ts('2026-10-01T04:00:00Z')),
    'evaluation':(ts('2026-10-01T04:00:00Z'),ts('2026-10-10T04:00:00Z'))}
PROFILES=[dict(name=f'{direction}_{lookback}m_{exitkind}',direction=direction,lookback=lookback,exitkind=exitkind)
    for direction in ('follow','fade') for lookback in (5,15) for exitkind in ('hold5','hold15','bracket8')]

def prepare():
    events={};quality=C.Counter();sources=[]
    # No filtering by whether the old strategy qualified or won.
    index=read(OLD/'pregame_index.json')
    for g in index:
        if len(g['markets'])!=2:quality['not_two_outcome']+=1;continue
        paths=[N/'sports'/g['asset']/(g['event']+'_candles.json.gz'),B/g['asset']/(g['event']+'_candles.json.gz'),T/g['asset']/(g['event']+'_candles.json.gz')]
        existing=[p for p in paths if p.exists()]
        if not existing:quality['no_full_game_tape']+=1;continue
        m=min(g['markets'],key=lambda m:m['ticker']);cs={};used=[]
        for path in existing:
            d=read(path);bars=d.get(m['ticker'],[])
            for c in bars:cs[int(c['end_period_ts'])]=c
            used.append(str(path));sources.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        rows={}
        for t,c in cs.items():
            q=quote(c);op=quote(c,'open')
            rows[t]=dict(close=q,open=op,volume=float(c.get('volume_fp') or c.get('volume') or 0))
        settled=ts(m['settlement_ts']);close=ts(m['close_time']);payout=float(m['result']=='yes') if m.get('result') in ('yes','no') else float(m['settlement_value_dollars'])
        # Scheduled starts are archived/revised; no retrospective play-state feature.
        start=g['start'];available=sum(t>=start and r['close'] is not None for t,r in rows.items())
        if available<16:quality['insufficient_inplay_tape']+=1;continue
        owner=g['owner']
        if owner in events:quality['duplicate_physical_game']+=1;continue
        events[owner]=dict(owner=owner,event=g['event'],asset=g['asset'],ticker=m['ticker'],start=start,close=close,
            settled=settled,payout=payout,mult=g['mult'],rows=rows,sources=used)
        quality['games']+=1;quality['quote_minutes']+=available
    save('sports_coverage.json',dict(counts=quality,assets=C.Counter(g['asset'] for g in events.values()),
        dates={a:dict(C.Counter(day(g['start']) for g in events.values() if g['asset']==a)) for a in sorted({g['asset'] for g in events.values()})},
        inputs=sources))
    (P/'data/sports_games.pickle').write_bytes(pickle.dumps(list(events.values())))
    print('PREPARED',dict(quality),flush=True)

def sideq(q,side):return q if side==1 else (1-q[1],1-q[0])
def exit_plan(g,at,side,entry,profile,delay):
    rows=g['rows'];hold=300 if profile['exitkind']=='hold5' else 900
    deadline=at+hold;pending=False;reason='time'
    for t in sorted(k for k in rows if k>at):
        if t>=g['close']:break
        q=rows[t]['close']
        if q is None:continue
        bid=sideq(q,side)[0]
        if not pending:
            if profile['exitkind']=='bracket8' and bid<=entry-.08+1e-8:pending=True;reason='stop'
            elif profile['exitkind']=='bracket8' and bid>=entry+.08-1e-8:pending=True;reason='target'
            elif t>=deadline:pending=True;reason='time'
        if not pending:continue
        when=t+(60 if delay else 3)
        if when>=g['close']:break
        nq=rows.get(t+60,{}).get('close' if delay else 'open')
        if nq is None:continue
        price=min(bid,sideq(nq,side)[0])
        return dict(exit_t=when,exit_price=round(price,8),exit_kind=reason,exit_decision=t,exit_bid=bid,next_exit_bid=sideq(nq,side)[0])
    return dict(exit_t=g['settled'],exit_price=g['payout'] if side==1 else 1-g['payout'],exit_kind='settlement',exit_decision=None)

def candidates(games,profile,delay=False):
    out=[];reject=C.Counter()
    for g in games:
        rows=g['rows'];times=sorted(t for t in rows if g['start']+profile['lookback']*60<=t<min(g['close'],g['start']+6*3600))
        for t in times:
            look=[rows.get(k,{}).get('close') for k in range(t-profile['lookback']*60,t+1,60)]
            if any(q is None for q in look):reject['incomplete_lookback']+=1;continue
            delta=sum(look[-1])/2-sum(look[0])/2
            if abs(delta)<.10-1e-8:continue
            side=(1 if delta>0 else -1)*(1 if profile['direction']=='follow' else -1)
            bid,ask=sideq(look[-1],side)
            volume=sum(rows.get(k,{}).get('volume',0) for k in range(t-540,t+1,60))
            if not .10-1e-8<=ask<=.9+1e-8 or ask-bid>.03000001 or volume<5:reject['band_spread_volume']+=1;continue
            n=rows.get(t+60,{}).get('close' if delay else 'open');when=t+(60 if delay else 3)
            if n is None or when>=g['close']:reject['missing_or_closed']+=1;continue
            price=max(ask,sideq(n,side)[1]);cap=min(.92,ask+.02)
            if price>cap+1e-8:reject['entry_cap']+=1;continue
            ex=exit_plan(g,when,side,price,profile,delay)
            if ex['exit_t']<=when:reject['invalid_exit']+=1;continue
            out.append(dict(owner=g['owner'],asset=g['asset'],ticker=g['ticker'],side=side,mult=g['mult'],
                t=t,entry_t=when,entry=round(price,8),ask=ask,cap=cap,delta=delta,lookback=profile['lookback'],prior_volume=volume,
                profile=profile['name'],source_start=g['start'],market_close=g['close'],**ex))
    return out,dict(reject)

def run(action):
    games=pickle.loads((P/'data/sports_games.pickle').read_bytes());out={};ledgers={}
    periods=['development','selection'] if action=='select' else ['evaluation']
    # Build only requested cohorts before evaluating later data.
    activegames=[g for g in games if any(a-86400<=g['start']<b for a,b in (PERIODS[per] for per in periods))]
    for profile in PROFILES:
        for delay in (False,True):
            pool,rej=candidates(activegames,profile,delay)
            for per in periods:
                lo,hi=PERIODS[per]
                for cost in (.01,.02):
                    rows,diag=portfolio([r for r in pool if lo<=r['entry_t']<hi],1,cost,sports=True)
                    k=profile['name']+'/'+('delay60' if delay else 'base')+'/'+per+f'/cost{int(cost*100)}'
                    out[k]=dict(metrics(rows,(hi-lo)/86400),diagnostics=diag,candidate_count=sum(lo<=r['entry_t']<hi for r in pool),rejections=rej)
                    ledgers[k]=rows
            if not delay:print(action,profile['name'],{per:{k:out[profile['name']+'/base/'+per+'/cost1'][k] for k in ('trades','net','trades_per_day')} for per in periods},flush=True)
    save('sports_'+action+'_results.json',out);save('sports_'+action+'_ledgers.json.gz',ledgers)
    if action=='select':
        good=[]
        for profile in PROFILES:
            ks=[profile['name']+'/base/selection/cost'+str(c) for c in (1,2)]
            if all(out[k]['net']>0 and out[k]['trades_per_day']>=10 for k in ks):good.append(profile['name'])
        good.sort(key=lambda n:out[n+'/base/selection/cost2']['net'],reverse=True)
        save('sports_selection_frozen.json',dict(chosen=good[0] if good else None,passing=good,trials=len(PROFILES),
            criterion='selection >=10/calendar day and positive net at1c/2c per transaction; rank2c net',
            frozen_utc=dt.datetime.now(dt.timezone.utc).isoformat(),later_not_evaluated=True))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','select','evaluate']);args=p.parse_args()
    if args.action=='prepare':prepare()
    else:run(args.action)
