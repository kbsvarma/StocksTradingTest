"""Every-game spread/total exposure at pre5: frequent secondary sports markets."""
from core import *
from recurring import choices,execute

def collect():
    games=read(P/'pregame_index.json');gs={g['asset'][:-4]:g['asset'] for g in games if g['asset'].endswith('GAME')};volume=defaultdict(float)
    for line in (P/'data/events.jsonl').read_text().splitlines():
        e=json.loads(line);s=e['series_ticker']
        if any(s==prefix+suffix for prefix in gs for suffix in ['TOTAL','SPREAD']):volume[s]+=sum(float(m.get('volume_24h_fp') or m.get('volume_24h') or 0) for m in e.get('markets',[]))
    names=sorted(volume,key=volume.get,reverse=True)[:8];save(P/'lines_selection.json',{'series':names,'method':'Top8 full-game spread/total series by current open24h volume, before replay; pre5 only.'})
    milemap={et:g for g in games for et in set(g['milestone'].get('related_event_tickers',[])+g['milestone'].get('primary_event_tickers',[]))};index=[];gaps=[];jobs=[]
    for s in names:
        ms=paged('/markets',{'series_ticker':s,'status':'settled','min_close_ts':ts('2026-09-11T04:00:00Z'),'max_close_ts':ts('2026-10-11T04:00:00Z'),'limit':1000},'markets');save(P/'lines'/s/'markets.json',ms);meta=get('/series/'+s)['series'];save(P/'lines'/s/'series.json',meta);groups=defaultdict(list)
        for m in ms:groups[m['event_ticker']].append(m)
        for et,items in groups.items():
            g=milemap.get(et)
            if not g:gaps.append({'event':et,'reason':'no_matching_schedule'});continue
            if not '2026-09-12'<=g['date']<='2026-10-09':continue
            if any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):gaps.append({'event':et,'reason':'outcome'});continue
            t=(g['start']-300)//60*60;row={'asset':s,'owner':et,'event':et,'date':g['date'],'start':g['start'],'t':t,'markets':items,'mult':float(meta['fee_multiplier']),'game_owner':g['owner']};index.append(row);jobs.append(row)
    requests=defaultdict(list)
    for g in jobs:requests[g['t']]+=g['markets']
    for i,(t,items) in enumerate(sorted(requests.items()),1):
        path=P/'lines_quotes'/(str(t)+'.json.gz')
        if path.exists():continue
        ms=list({m['ticker']:m for m in items}.values());out={}
        for n in range(0,len(ms),100):
            x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in ms[n:n+100]),'start_ts':t-600,'end_ts':t+60,'period_interval':1});out.update({v['market_ticker']:v['candlesticks'] for v in x.get('markets',[])})
        save(path,out)
        if i%40==0:print('LINE_QUOTES',i,'/',len(requests),flush=True)
    save(P/'lines_index.json',index);save(P/'lines_gaps.json',gaps);print('LINES_WINDOWS',len(index),flush=True)
def main():
    index=read(P/'lines_index.json');cand=defaultdict(list);skips=defaultdict(Counter)
    for g in index:
        t=g['t'];cm={k:{c['end_period_ts']:c for c in v} for k,v in read(P/'lines_quotes'/(str(t)+'.json.gz')).items()};probs={}
        for m in g['markets']:
            q=qte(cm.get(m['ticker'],{}).get(t))
            if q:probs[m['ticker']]=sum(q)/2
        opts=choices(g['markets'],cm,t,g['asset'],g['owner'],'lines',g['mult'],probs,{'date':g['date'],'scheduled_start':g['start'],'game_owner':g['game_owner']})
        yes=[x for x in opts if x['side']=='yes'];atm=min(yes,key=lambda x:(abs(x['mid']-.5),x['ticker'])) if yes else None
        for policy in ['yes','no','favorite','underdog']:
            for mode in ['base','delay60']:
                key=g['asset']+'/'+policy+'/'+mode
                if not atm:skips[key]['no_quote']+=1;continue
                side=policy if policy in ('yes','no') else ('yes' if (atm['mid']>=.5)==(policy=='favorite') else 'no');pick=next(x for x in opts if x['ticker']==atm['ticker'] and x['side']==side)
                row,reason=execute(pick,t,mode)
                if row and row['entered']<g['start']:cand[key].append(row)
                else:skips[key][reason or 'after_start']+=1
    result={};ledgers={}
    for key in sorted(set(cand)|set(skips)):
        rows=cand.get(key,[]);asset=key.split('/')[0];ws=[w for w in index if w['asset']==asset]
        for part in ['all','before_oct','october']:
            choose=lambda d:part=='all' or (d<'2026-10-01' if part=='before_oct' else d>='2026-10-01');rr=[r for r in rows if choose(r['date'])];den=sum(choose(w['date']) for w in ws)
            for q in [1,5]:
                for cost in [.01,.02]:record(result,ledgers,key+'/'+part+f'/q{q}/cost{round(cost*100)}',rr,den,q,cost)
    save(P/'lines_results.json',result);save(P/'lines_candidates.json.gz',cand);save(P/'lines_ledgers.json.gz',ledgers);save(P/'lines_skips.json',{k:dict(v) for k,v in skips.items()})
    for k,v in sorted(result.items(),key=lambda kv:kv[1]['net'],reverse=True):
        if '/base/all/q1/cost1' in k:print(k,{z:v[z] for z in ['windows','trades','net','participation','drawdown']},flush=True)
if __name__=='__main__':
    import sys
    collect() if 'collect' in sys.argv else main()
