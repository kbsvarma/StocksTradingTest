"""Backward, outcome-selected validation; no orders, no production imports."""
from core import *
import daily,pregame
ROOT=P

def wnba_collect():
    dest=ROOT/'wnba_extension';dest.mkdir(exist_ok=True);s='KXWNBAGAME'
    ms=paged('/markets',{'series_ticker':s,'status':'settled','min_close_ts':ts('2026-04-30T04:00:00Z'),'max_close_ts':ts('2026-10-11T04:00:00Z'),'limit':1000},'markets');save(dest/'markets.json',ms)
    miles=paged('/milestones',{'category':'Sports','type':'basketball_game','minimum_start_date':'2026-04-30T00:00:00Z','limit':500},'milestones');save(dest/'milestones.json',miles)
    meta=get('/series/'+s)['series'];save(dest/'series.json',meta);mapping=defaultdict(list);groups=defaultdict(list)
    for m in miles:
        et=m.get('details',{}).get('main_game_event_ticker')
        if et:mapping[et].append(m)
    for m in ms:groups[m['event_ticker']].append(m)
    index=[];gaps=[]
    for et,items in sorted(groups.items()):
        mm=mapping[et]
        if len(mm)!=1:gaps.append({'event':et,'reason':'missing_ambiguous_schedule'});continue
        mi=mm[0];start=ts(mi['start_date']);date=day(start)
        if not '2026-05-01'<=date<='2026-10-09':continue
        if len(items)!=2 or any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):gaps.append({'event':et,'reason':'outcome'});continue
        if start>=min(ts(m['close_time']) for m in items):gaps.append({'event':et,'reason':'schedule_after_close'});continue
        index.append({'asset':s,'owner':mi['id'],'event':et,'date':date,'start':start,'markets':items,'mult':float(meta['fee_multiplier']),'season_type':mi.get('details',{}).get('season_type'),'milestone':mi,'begin':'2026-05-01'})
    for i,g in enumerate(index):
        path=dest/'pregame'/s/(g['event']+'_quotes.json.gz')
        if path.exists():continue
        ticks=[m['ticker'] for m in g['markets']];start=g['start'];x=get('/markets/candlesticks',{'market_tickers':','.join(ticks),'start_ts':start-1860,'end_ts':start-180,'period_interval':1})
        save(path,{v['market_ticker']:v['candlesticks'] for v in x.get('markets',[])})
        if i%50==0:print('WNBA_QUOTE',i,len(index),flush=True)
    save(dest/'pregame_index.json',index);save(dest/'pregame_gaps.json',gaps)
    pregame.P=dest;pregame.main()

if __name__=='__main__':
    import sys
    if 'daily' in sys.argv:
        daily.P=ROOT/'daily_extension';daily.P.mkdir(exist_ok=True)
        daily.collect(['KXAAAGASD','KXHIGHNY'],'2026-05-01');daily.main()
    else:wnba_collect()
