from core import *
from recurring import choices,execute
ROOT=P.parent
def collect():
    s='KXCS2GAME';meta=get('/series/'+s)['series'];save(P/'cs2_series.json',meta)
    ms=paged('/markets',{'series_ticker':s,'status':'settled','min_close_ts':ts('2026-08-14T04:00:00Z'),'max_close_ts':ts('2026-10-11T04:00:00Z'),'limit':1000},'markets');save(P/'cs2_markets.json',ms)
    miles=paged('/milestones',{'category':'Esports','minimum_start_date':'2026-08-14T00:00:00Z','limit':500},'milestones');save(P/'cs2_milestones.json',miles);mapping=defaultdict(list)
    for mi in miles:
        et=mi.get('details',{}).get('main_game_event_ticker')
        if et:mapping[et].append(mi)
    groups=defaultdict(list)
    for m in ms:groups[m['event_ticker']].append(m)
    index=[];gaps=[];jobs=[]
    for et,items in groups.items():
        mm=mapping.get(et,[])
        if len(mm)!=1:gaps.append({'event':et,'reason':'schedule_missing'});continue
        mi=mm[0];start=ts(mi['start_date']);date=day(start)
        if not '2026-08-15'<=date<='2026-10-09':continue
        if len(items)!=2 or any(not m.get('settlement_ts') or pay(m,'yes') is None for m in items):gaps.append({'event':et,'reason':'outcomes'});continue
        if start>=min(ts(m['close_time']) for m in items):gaps.append({'event':et,'reason':'schedule_after_close'});continue
        index.append({'event':et,'asset':s,'owner':mi['id'],'start':start,'date':date,'markets':items,'mult':float(meta['fee_multiplier']),'milestone':mi})
        if not (P/'cs2_quotes'/(et+'.json.gz')).exists():jobs.append((et,items,start-960,start+720))
    batches=[];batch=[];nt=0;lo=hi=0
    for job in sorted(jobs,key=lambda z:z[2]):
        a,b=(min(lo,job[2]),max(hi,job[3])) if batch else job[2:];n=nt+len(job[1])
        if batch and (n>100 or n*((b-a)//60+2)>9000):batches.append((batch,lo,hi));batch=[];nt=0;a,b=job[2:]
        batch.append(job);nt+=len(job[1]);lo,hi=a,b
    if batch:batches.append((batch,lo,hi))
    print('CS2_EXTENDED',len(index),'batches',len(batches),'gaps',len(gaps),flush=True)
    for n,(batch,lo,hi) in enumerate(batches,1):
        ticks=[m['ticker'] for job in batch for m in job[1]];x=get('/markets/candlesticks',{'market_tickers':','.join(ticks),'start_ts':lo,'end_ts':hi,'period_interval':1});cm={v['market_ticker']:v['candlesticks'] for v in x.get('markets',[])}
        for et,items,a,b in batch:save(P/'cs2_quotes'/(et+'.json.gz'),{m['ticker']:[c for c in cm.get(m['ticker'],[]) if a<=c['end_period_ts']<=b] for m in items})
        if n%25==0:print('CS2_FETCHED',n,'/',len(batches),flush=True)
    save(P/'cs2_index.json',index);save(P/'cs2_gaps.json',gaps)
def main():
    datasets={};cs2=[];nfl=[];old=ROOT/'kalshi_nfl_nhl_frequency_2026_10_10/data/KXNFLGAME';groups=defaultdict(list)
    for m in read(old/'markets.json'):groups[m['event_ticker']].append(m)
    for et,ms in groups.items():
        mi=read(old/(et+'_milestone.json'));start=ts(mi['start_date'])
        if mi['details']['season_type'] not in ('REG','POST'):continue
        nfl.append({'event':et,'asset':'KXNFLGAME','owner':mi['id'],'start':start,'date':day(start),'markets':ms,'mult':1.,'_quotes':old/(et+'_candles.json.gz')})
    for g in read(P/'cs2_index.json'):cs2.append({**g,'_quotes':P/'cs2_quotes'/(g['event']+'.json.gz')})
    datasets={'NFL':nfl,'CS2':cs2};candidates=defaultdict(list);skips=defaultdict(Counter);windows=[]
    for league,games in datasets.items():
        for g in games:
            windows.append({k:g[k] for k in ['event','asset','owner','date']});cm={k:{c['end_period_ts']:c for c in v} for k,v in read(g['_quotes']).items()}
            for timing in (['pre5'] if league=='NFL' else ['pre5','post5','arrival']):
                first=(g['start']+(-300 if timing!='post5' else 300))//60*60;times=[first] if timing!='arrival' else range(first,(g['start']+600)//60*60+1,60)
                for mode in ['base','delay60']:
                    key=league+'/'+timing+'/'+mode;reason='missing_outcome_quotes';actual=None
                    for t in times:
                        probs={}
                        for m in g['markets']:
                            q=qte(cm.get(m['ticker'],{}).get(t))
                            if q:probs[m['ticker']]=sum(q)/2
                        opts=choices(g['markets'],cm,t,g['asset'],g['owner'],'followup',g['mult'],probs,{'date':g['date'],'scheduled_start':g['start'],'window_first':first,'window_deadline':(g['start']+600)//60*60 if timing=='arrival' else first})
                        yes=[x for x in opts if x['side']=='yes']
                        if len(yes)!=len(g['markets']):reason='missing_outcome_quotes';continue
                        fav=max(yes,key=lambda x:(x['mid'],x['ticker']));pick=fav if league=='CS2' else next(x for x in opts if x['ticker']==fav['ticker'] and x['side']=='no')
                        actual,reason=execute(pick,t,mode)
                        if actual:break
                    if actual:candidates[key].append(actual)
                    else:skips[key][reason]+=1
    results={};ledgers={}
    for key,rows in candidates.items():
        league=key.split('/')[0];ws=[w for w in windows if ('NFL' in w['asset'])==(league=='NFL')]
        parts=['all','older','recent','october']
        for part in parts:
            split='2026-08-01' if league=='NFL' else '2026-09-26'
            pick=lambda d:part=='all' or (d<split if part=='older' else d>=split if part=='recent' else d>='2026-10-01')
            rr=[r for r in rows if pick(r['date'])];den=len({w['owner'] for w in ws if pick(w['date'])})
            for qty in [1,2,5]:
                for stress in [.01,.02]:record(results,ledgers,key+'/'+part+f'/q{qty}/cost{round(stress*100)}',rr,den,qty,stress)
    save(P/'followup_candidates.json.gz',candidates);save(P/'followup_windows.json',windows);save(P/'followup_skips.json',{k:dict(v) for k,v in skips.items()});save(P/'followup_results.json',results);save(P/'followup_ledgers.json.gz',ledgers)
    for k,v in results.items():
        if '/base/' in k and '/q1/cost1' in k:print(k,{z:v[z] for z in ['windows','trades','wins','losses','scalar','net','participation','drawdown']},flush=True)
if __name__=='__main__':
    import sys
    collect() if 'collect' in sys.argv else main()
