"""Extend two pre-existing NFL rules; no tuning or order interfaces."""
from study import *
from datetime import datetime, timezone
from pathlib import Path
import re

OLD = P.parent.parent / 'kalshi_nfl_nhl_frequency_2026_10_10'
SERIES = 'KXNFLGAME'
START_NEW = ts('2026-10-09T04:00:00Z')
NOW = int(time.time())

def external(url, name):
    target=P/'external'/(name+'.json')
    if target.exists():return read(target)['response']
    response=requests.get(url,timeout=25);response.raise_for_status()
    body=response.json();save(target,dict(url=url,retrieved=time.time(),response=body))
    return body

def canon(a):
    return {'LA':'LAR','WSH':'WAS','JAC':'JAX','OAK':'LV','SD':'LAC'}.get(a,a)

def main():
    markets=paged('/markets',dict(series_ticker=SERIES,status='settled',min_close_ts=START_NEW,max_close_ts=NOW,limit=1000),'markets')
    save(P/'current_markets.json',markets)
    groups=defaultdict(list)
    old_events={m['event_ticker'] for m in read(OLD/'data'/SERIES/'markets.json')}
    for m in markets:
        if m['event_ticker'] not in old_events:groups[m['event_ticker']].append(m)
    metadata=get('/series/'+SERIES)['series'];save(P/'series.json',metadata)
    fees=get('/series/fee_changes',dict(series_ticker=SERIES))
    save(P/'series_fee_changes_current.json',fees)
    import late
    late.FEE_CHANGES=fees.get('series_fee_change_arr',late.FEE_CHANGES)
    candidates={2:defaultdict(list),5:defaultdict(list)};coverage=[]
    for event,items in sorted(groups.items()):
        receipt=dict(event=event,markets=len(items))
        try:
            if len(items)!=2:raise ValueError('not exactly two outcome markets')
            token=re.search(r'-(\d{2}[A-Z]{3}\d{2})',event).group(1)
            date=datetime.strptime(token,'%y%b%d').strftime('%Y%m%d')
            if date<'20261009':continue
            schedule=external('https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates='+date+'&limit=100','schedule_'+date)
            teams={canon(m['ticker'].rsplit('-',1)[1]):next(iter(m['custom_strike'].values())) for m in items}
            matches=[]
            for game in schedule.get('events',[]):
                parts={r['homeAway']:canon(r['team']['abbreviation']) for r in game['competitions'][0]['competitors']}
                if set(parts.values())==set(teams):matches.append((game,parts))
            if len(matches)!=1:raise ValueError('ambiguous/unmatched schedule: '+str(len(matches)))
            game,parts=matches[0]
            if not game['status']['type']['completed']:raise ValueError('source game not complete')
            summary=external('https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event='+game['id'],'summary_'+game['id'])
            head=summary['header'];comp=head['competitions'][0]
            assert {r['homeAway']:canon(r['team']['abbreviation']) for r in comp['competitors']}==parts
            season={1:'PRE',2:'REG',3:'POST'}.get((head.get('season') or game.get('season') or {}).get('type'),'unknown')
            assert season in ('REG','POST'),season
            mapping={r['team']['id']:teams[canon(r['team']['abbreviation'])] for r in comp['competitors']}
            start=ts(game['date']);end=max(ts(m['close_time']) for m in items)
            assert end>start,'market close predates kickoff; do not infer corrected close'
            mile=dict(id='espn:'+game['id'],start_date=game['date'],end_date=datetime.fromtimestamp(end,timezone.utc).isoformat(),details=dict(main_game_event_ticker=event,home_team_id=teams[parts['home']],away_team_id=teams[parts['away']],season_type=season,espn_team_mapping=mapping))
            response=get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in items),start_ts=start-3600,end_ts=min(max(end+120,start+8*3600),start+12*3600),period_interval=1))
            candles={r['market_ticker']:r['candlesticks'] for r in response.get('markets',[])}
            save(P/'data'/(event+'_inputs.json'),dict(markets=items,milestone=mile,candles=candles))
            for spread in (2,5):
                out,cov,rej=event_candidates(items,candles,mile,SERIES,summary,metadata['fee_multiplier'],spread/100)
                receipt['coverage']=cov;receipt['rejections']=rej
                for key,rows in out.items():
                    if key.startswith('p90/'):
                        candidates[spread][key]+=[r for r in rows if accepts(r,'leader',5)]
            receipt.update(status='ok',start=start,espn_id=game['id'])
        except Exception as exc:receipt.update(status='error',error=str(exc))
        coverage.append(receipt);print('GAME',event,receipt['status'],receipt.get('error',''),flush=True)
    save(P/'coverage.json',coverage);save(P/'new_candidates.json.gz',candidates)
    old2=read(OLD/'candidates.json.gz');old5=read(OLD/'operational_candidates_spread5.json.gz')
    results={};ledgers={}
    for spread in (2,5):
        for label,lag,mode,friction in SCENARIOS:
            key='p90/'+('base' if label=='cost2c' else label)
            old=[r for r in (old2 if spread==2 else old5).get(SERIES+'/'+key,[]) if accepts(r,'leader',5) and period(r,'all_regular',SERIES)]
            fresh=candidates[spread].get(key,[])
            for part,rows in [('previous_record',old),('fresh_extension',fresh),('combined',old+fresh)]:
                trades,blocked=portfolio(rows,friction)
                ident=f'spread{spread}/{label}/{part}'
                results[ident]=dict(**metrics(trades),blocked=blocked)
                ledgers[ident]=trades
    prior=read(OLD/'results.json');prior5=read(OLD/'operational_results.json')
    for label,*_ in SCENARIOS:
        for spread,source,key in [(2,prior,f'{SERIES}/leader/m5/p90/{label}/all_regular'),(5,prior5,f'spread5/{SERIES}/leader/m5/p90/{label}/all_regular')]:
            a=source[key];b=results[f'spread{spread}/{label}/previous_record']
            assert all(abs(a[k]-b[k])<.0001 for k in ('trades','wins','losses','net','drawdown')),(key,a,b)
    save(P/'results.json',results);save(P/'ledgers.json.gz',ledgers)
    save(P/'validation.json',dict(previous_results_reproduced=True,prior_code_frozen=True,requested_events=len(groups),games=coverage,asof=NOW))
    print('RESULTS',json.dumps({k:v for k,v in results.items() if '/previous_record' not in k}),flush=True)

if __name__=='__main__':main()
