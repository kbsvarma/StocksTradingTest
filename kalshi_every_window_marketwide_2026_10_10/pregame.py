"""Every-game decisions, broad leagues selected by current volume, not outcomes."""
from core import *
from recurring import choices,execute
ROOT=P.parent;BASE=ROOT/'kalshi_noncrypto_consistency_2026_10_10';BROAD=ROOT/'kalshi_broad_review_2026_10_10'
def collect():
    ag=defaultdict(float)
    for line in (P/'data/events.jsonl').read_text().splitlines():
        e=json.loads(line);s=e['series_ticker']
        if e.get('category') in ('Sports','Esports') and s.endswith(('GAME','MATCH')):
            ag[s]+=sum(float(m.get('volume_24h_fp') or m.get('volume_24h') or 0) for m in e.get('markets',[]))
    names=sorted(set(sorted(ag,key=ag.get,reverse=True)[:20])|{'KXWNBAGAME','KXCS2GAME'})
    save(P/'pregame_selection.json',{'method':'Top20 current public open-game-series 24h volume plus WNBA and CS2 comparators. No historical P&L selection.','series':names,'snapshot_volume':dict(ag)})
    miles=read(BASE/'milestones_all.json');byevent=defaultdict(list)
    for m in miles:
        et=m.get('details',{}).get('main_game_event_ticker')
        if et:byevent[et].append(m)
    index=[];gaps=[];jobs=[]
    for s in names:
        d=P/'pregame'/s;d.mkdir(parents=True,exist_ok=True)
        old=BASE/'sports'/s;old2=BROAD/'replays'/s
        if (old/'markets.json').exists():ms=read(old/'markets.json');begin='2026-08-15';source=old
        elif (old2/'markets.json').exists() and s not in ('KXEPLGAME',):ms=read(old2/'markets.json');begin='2026-09-26';source=old2
        else:
            begin='2026-09-12';source=None;file=d/'markets.json'
            ms=read(file) if file.exists() else paged('/markets',{'series_ticker':s,'status':'settled','min_close_ts':ts('2026-09-10T04:00:00Z'),'max_close_ts':ts('2026-10-11T04:00:00Z'),'limit':1000},'markets')
        save(d/'markets.json',ms);meta=get('/series/'+s)['series'];save(d/'series.json',meta);groups=defaultdict(list)
        for m in ms:groups[m['event_ticker']].append(m)
        for et,items in groups.items():
            mm=byevent.get(et,[])
            if source and (source/(et+'_milestone.json')).exists():mm=[read(source/(et+'_milestone.json'))]
            elif source and (source/(et+'_milestones.json')).exists():mm=[m for m in read(source/(et+'_milestones.json')) if m.get('details',{}).get('main_game_event_ticker')==et]
            if len(mm)!=1:gaps.append({'series':s,'event':et,'reason':'missing_or_ambiguous_schedule'});continue
            mi=mm[0]
            if not mi.get('start_date'):gaps.append({'series':s,'event':et,'reason':'missing_start'});continue
            start=ts(mi['start_date']);date=day(start)
            if not begin<=date<='2026-10-09':continue
            if not 2<=len(items)<=3 or any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):gaps.append({'series':s,'event':et,'reason':'incomplete_outcomes'});continue
            if start>=min(ts(m['close_time']) for m in items):gaps.append({'series':s,'event':et,'reason':'start_after_recorded_close'});continue
            row={'asset':s,'owner':mi['id'],'event':et,'date':date,'start':start,'markets':items,'mult':float(meta['fee_multiplier']),'season_type':mi.get('details',{}).get('season_type'),'milestone':mi,'begin':begin};index.append(row)
            path=d/(et+'_quotes.json.gz')
            if path.exists():continue
            jobs.append((s,et,items,start-1860,start-180))
        print('PREGAME_CATALOG',s,len(groups),'matched cumulative',len(index),flush=True)
    # Nearby games share bounded public quote requests; max100 contracts/9000 bars.
    batches=[];batch=[];nt=0;lo=hi=0
    for job in sorted(jobs,key=lambda r:r[3]):
        a,b=(min(lo,job[3]),max(hi,job[4])) if batch else (job[3],job[4]);n=nt+len(job[2])
        if batch and (n>100 or n*((b-a)//60+2)>9000):batches.append((batch,lo,hi));batch=[];nt=0;a,b=job[3:]
        batch.append(job);nt+=len(job[2]);lo,hi=a,b
    if batch:batches.append((batch,lo,hi))
    for n,(batch,lo,hi) in enumerate(batches,1):
        try:
            ticks=[m['ticker'] for job in batch for m in job[2]];x=get('/markets/candlesticks',{'market_tickers':','.join(ticks),'start_ts':lo,'end_ts':hi,'period_interval':1});cm={v['market_ticker']:v['candlesticks'] for v in x.get('markets',[])}
            for s,et,items,a,b in batch:save(P/'pregame'/s/(et+'_quotes.json.gz'),{m['ticker']:[c for c in cm.get(m['ticker'],[]) if a<=c['end_period_ts']<=b] for m in items})
        except Exception as ex:gaps.append({'reason':'quote_request','events':[r[1] for r in batch],'error':str(ex)})
        if n%20==0:print('PREGAME_QUOTES',n,'/',len(batches),flush=True)
    save(P/'pregame_index.json',index);save(P/'pregame_gaps.json',gaps);print('PREGAME_DONE',len(index),len(gaps),flush=True)

def main():
    index=read(P/'pregame_index.json');cand=defaultdict(list);skips=defaultdict(Counter);samples=defaultdict(list);windows=[]
    # Build each favorite's decision-only quote and its later resolved label.
    snapshots=[]
    for g in index:
        file=P/'pregame'/g['asset']/(g['event']+'_quotes.json.gz');cm={k:{c['end_period_ts']:c for c in v} for k,v in read(file).items()} if file.exists() else {}
        windows.append({k:g[k] for k in ['owner','asset','date','event','begin','season_type']})
        for minutes in [30,5]:
            t=(g['start']-minutes*60)//60*60;probs={}
            for m in g['markets']:
                q=qte(cm.get(m['ticker'],{}).get(t))
                if q:probs[m['ticker']]=sum(q)/2
            opts=choices(g['markets'],cm,t,g['asset'],g['owner'],'pregame',g['mult'],probs,{'date':g['date'],'minutes_before_start':minutes,'scheduled_start':g['start']})
            yes=[x for x in opts if x['side']=='yes']
            if len(yes)!=len(g['markets']):snapshots.append((g,minutes,t,[],None));continue
            fav=max(yes,key=lambda x:(x['mid'],x['ticker']));samples[(g['asset'],minutes)].append({'t':t,'p':fav['mid'],'y':pay(fav['_m'],'yes'),'settled':max(ts(m['settlement_ts']) for m in g['markets'])})
            snapshots.append((g,minutes,t,opts,fav))
    calibration={}
    for g,minutes,t,opts,fav in snapshots:
        for policy in ['favorite','not_favorite','underdog','calibrated']:
            for mode in ['base','delay60']:
                key=g['asset']+f'/pre{minutes}/'+policy+'/'+mode
                if fav is None:skips[key]['missing_outcome_quote']+=1;continue
                py=fav['mid'];boundary=ts(day(t)+'T00:00:00-04:00');binid=min(3,int(max(0,py-.4)/.15));ck=(g['asset'],minutes,boundary,binid)
                if ck not in calibration:
                    train=[r for r in samples[(g['asset'],minutes)] if boundary-28*86400<=r['t']<boundary and r['settled']<boundary and min(3,int(max(0,r['p']-.4)/.15))==binid]
                    calibration[ck]={'n':len(train),'observed':sum(r['y'] for r in train),'sum_p':sum(r['p'] for r in train),'latest_settlement':max((r['settled'] for r in train),default=None)}
                fit=calibration[ck];delta=(fit['observed']-fit['sum_p'])/(fit['n']+40);py=min(.999,max(.001,py+delta))
                if policy=='favorite':pick=fav
                elif policy=='not_favorite':pick=next(x for x in opts if x['ticker']==fav['ticker'] and x['side']=='no')
                elif policy=='underdog':pick=min([x for x in opts if x['side']=='yes'],key=lambda x:(x['mid'],x['ticker']))
                else:
                    pool=[x for x in opts if x['ticker']==fav['ticker']];pick=max(pool,key=lambda x:((py if x['side']=='yes' else 1-py)-x['ask']-fee(x['ask'],1,x['mult']),x['side']))
                row,reason=execute(pick,t,mode)
                if row and row['entered']<g['start']:
                    row.update(calibration_n=fit['n'],calibration_boundary=boundary,calibration_last_settlement=fit['latest_settlement'],calibrated_favorite_probability=py,season_type=g['season_type']);cand[key].append(row)
                else:skips[key][reason or 'after_start']+=1
    save(P/'pregame_candidates.json.gz',cand);save(P/'pregame_windows.json',windows);save(P/'pregame_skips.json',{k:dict(v) for k,v in skips.items()});save(P/'pregame_calibration.json',[dict(asset=k[0],minutes=k[1],boundary=k[2],bin=k[3],**v) for k,v in calibration.items()])
    results={};ledgers={}
    for key in sorted(set(cand)|set(skips)):
        rows=cand.get(key,[]);asset=key.split('/')[0];ws=[w for w in windows if w['asset']==asset]
        for part in ['all','before_oct','october']:
            pick=lambda d:part=='all' or (d<'2026-10-01' if part=='before_oct' else d>='2026-10-01')
            den=sum(pick(w['date']) for w in ws);rr=[r for r in rows if pick(r['date'])]
            for qty in [1,5]:
                for stress in [.01,.02]:record(results,ledgers,key+'/'+part+f'/q{qty}/cost{round(stress*100)}',rr,den,qty,stress)
    save(P/'pregame_results.json',results);save(P/'pregame_ledgers.json.gz',ledgers)
    top=[(k,v) for k,v in results.items() if '/base/all/q1/cost1' in k]
    for k,v in sorted(top,key=lambda z:z[1]['net'],reverse=True)[:20]:print('RESULT',k,{z:v[z] for z in ['windows','trades','wins','losses','net','participation','drawdown']},flush=True)
if __name__=='__main__':
    import sys
    if 'collect' in sys.argv:collect()
    else:main()
