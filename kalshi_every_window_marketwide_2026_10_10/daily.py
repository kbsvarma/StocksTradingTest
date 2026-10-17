"""Every-date tests for high-volume daily gas and regional weather families."""
from core import *
from recurring import choices,execute
import re
CENSUS=P
def collect(selected=None,start_date='2026-08-15'):
    series={s['ticker']:s for s in read(CENSUS/'data/series.json')['series']};volume=defaultdict(float)
    for line in (CENSUS/'data/events.jsonl').read_text().splitlines():
        e=json.loads(line);volume[e['series_ticker']]+=sum(float(m.get('volume_24h_fp') or m.get('volume_24h') or 0) for m in e.get('markets',[]))
    gas=sorted([s for s in volume if s.startswith('KXAAAGASD')],key=volume.get,reverse=True)[:8]
    temp=sorted([s for s in volume if s.startswith('KXHIGH') and series.get(s,{}).get('frequency')=='daily'],key=volume.get,reverse=True)[:8]
    names=selected or gas+temp+['KXRAIN'];save(P/'daily_selection.json',{'method':'Outcome-selected earlier-history follow-up' if selected else 'Eight highest current-volume gas series, eight daily-high temperature series, and rainfall. Selection precedes their backtests.','series':names,'start_date':start_date})
    index=[];gaps=[];requests_by_t=defaultdict(list)
    for s in names:
        d=P/'daily'/s;d.mkdir(parents=True,exist_ok=True);file=d/'markets.json'
        ms=read(file) if file.exists() else paged('/markets',{'series_ticker':s,'status':'settled','min_close_ts':ts(start_date+'T00:00:00Z')-86400,'max_close_ts':ts('2026-10-11T04:00:00Z'),'limit':1000},'markets')
        save(file,ms);meta=get('/series/'+s)['series'];save(d/'series.json',meta);groups=defaultdict(list)
        for m in ms:groups[m['ticker'] if s=='KXRAIN' else m['event_ticker']].append(m)
        for owner,items in groups.items():
            match=re.search(r'-(\d{2}[A-Z]{3}\d{2})(?:-|$)',owner)
            if not match:gaps.append({'owner':owner,'reason':'event_date'});continue
            dt=datetime.strptime(match.group(1),'%y%b%d').replace(tzinfo=ET);date=dt.date().isoformat()
            if not start_date<=date<='2026-10-09':continue
            if any(pay(m,'yes') is None or not m.get('settlement_ts') for m in items):gaps.append({'owner':owner,'reason':'outcome'});continue
            kind='gas' if s in gas else 'temperature' if s in temp else 'rain'
            times=[int((dt-timedelta(days=1)).replace(hour=h).timestamp()) for h in (20,23)] if kind=='gas' else [int(dt.replace(hour=h).timestamp()) for h in (12,16)]
            row={'owner':owner,'event':items[0]['event_ticker'],'asset':s,'family':kind,'date':date,'markets':items,'times':times,'mult':float(meta['fee_multiplier'])};index.append(row)
            for t in times:
                for m in items:
                    if ts(m['open_time'])<=t<ts(m['close_time']):requests_by_t[t].append(m)
        print('DAILY_CATALOG',s,len(groups),'cumulative windows',len(index),flush=True)
    requests_n=0
    for t,items in sorted(requests_by_t.items()):
        path=P/'daily_quotes'/(str(t)+'.json.gz')
        if path.exists():continue
        out={};ms=list({m['ticker']:m for m in items}.values())
        for n in range(0,len(ms),100):
            x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in ms[n:n+100]),'start_ts':t-600,'end_ts':t+60,'period_interval':1});out.update({v['market_ticker']:v['candlesticks'] for v in x.get('markets',[])});requests_n+=1
        save(path,out)
        if requests_n%40==0:print('DAILY_QUOTES',requests_n,flush=True)
    save(P/'daily_index.json',index);save(P/'daily_gaps.json',gaps);print('DAILY_COLLECTED',len(index),flush=True)
def main():
    index=read(P/'daily_index.json');quotes={int(p.stem.split('.')[0]):{k:{c['end_period_ts']:c for c in v} for k,v in read(p).items()} for p in (P/'daily_quotes').glob('*.json.gz')}
    snaps=[];samples=defaultdict(list);cand=defaultdict(list);skips=defaultdict(Counter);cal={}
    for g in index:
        for timing,t in enumerate(g['times']):
            cm=quotes.get(t,{});probs={}
            for m in g['markets']:
                if not ts(m['open_time'])<=t<ts(m['close_time']):continue
                q=qte(cm.get(m['ticker'],{}).get(t))
                if q:probs[m['ticker']]=sum(q)/2
            opts=choices(g['markets'],cm,t,g['asset'],g['owner'],'daily',g['mult'],probs,{'date':g['date'],'family':g['family']})
            if not opts:snaps.append((g,timing,t,[],None));continue
            # Temperatures are an exclusive bin ladder: pick highest YES.
            # Gas/rain choose the higher-priced side of the nearest-50c contract.
            if g['family']=='temperature':fav=max([x for x in opts if x['side']=='yes'],key=lambda x:(x['mid'],x['ticker']))
            else:
                atm=min([x for x in opts if x['side']=='yes'],key=lambda x:(abs(x['mid']-.5),x['ticker']));fav=max([x for x in opts if x['ticker']==atm['ticker']],key=lambda x:(x['mid'],x['side']))
            samples[(g['asset'],timing)].append({'t':t,'p':fav['mid'],'y':pay(fav['_m'],fav['side']),'settled':max(ts(m['settlement_ts']) for m in g['markets'])})
            snaps.append((g,timing,t,opts,fav))
    for g,timing,t,opts,fav in snaps:
        for policy in ['favorite','not_favorite','calibrated']:
            for mode in ['base','delay60']:
                key=g['asset']+f'/time{timing}/'+policy+'/'+mode
                if fav is None:skips[key]['quote_unavailable']+=1;continue
                boundary=int(datetime.fromtimestamp(t,ET).replace(hour=0,minute=0,second=0,microsecond=0).timestamp());binid=min(3,int(max(0,fav['mid']-.4)/.15));ck=(g['asset'],timing,boundary,binid)
                if ck not in cal:
                    tr=[r for r in samples[(g['asset'],timing)] if boundary-28*86400<=r['t']<boundary and r['settled']<boundary and min(3,int(max(0,r['p']-.4)/.15))==binid]
                    cal[ck]={'n':len(tr),'delta':sum(r['y']-r['p'] for r in tr)/(len(tr)+40),'last_settlement':max((r['settled'] for r in tr),default=None)}
                fitted=cal[ck];prob=max(.001,min(.999,fav['mid']+fitted['delta']));other=next(x for x in opts if x['ticker']==fav['ticker'] and x['side']!=fav['side'])
                if policy=='favorite':pick=fav
                elif policy=='not_favorite':pick=other
                else:pick=max([fav,other],key=lambda x:((prob if x is fav else 1-prob)-x['ask']-fee(x['ask'],1,x['mult']),x['side']))
                row,reason=execute(pick,t,mode)
                if row:row.update(calibration_boundary=boundary,calibration_last_settlement=fitted['last_settlement']);cand[key].append(row)
                else:skips[key][reason]+=1
    results={};ledgers={}
    for key in sorted(set(cand)|set(skips)):
        rows=cand.get(key,[]);asset=key.split('/')[0];ws=[w for w in index if w['asset']==asset]
        for part in ['all','before_oct','october']:
            pick=lambda d:part=='all' or (d<'2026-10-01' if part=='before_oct' else d>='2026-10-01')
            rr=[r for r in rows if pick(r['date'])];den=sum(pick(w['date']) for w in ws)
            for qty in [1,5]:
                for stress in [.01,.02]:record(results,ledgers,key+'/'+part+f'/q{qty}/cost{round(stress*100)}',rr,den,qty,stress)
    save(P/'daily_candidates.json.gz',cand);save(P/'daily_results.json',results);save(P/'daily_ledgers.json.gz',ledgers);save(P/'daily_skips.json',{k:dict(v) for k,v in skips.items()});save(P/'daily_calibration.json',[dict(asset=k[0],timing=k[1],boundary=k[2],bin=k[3],**v) for k,v in cal.items()])
    for k,v in sorted([(k,v) for k,v in results.items() if '/base/all/q1/cost1' in k],key=lambda kv:kv[1]['net'],reverse=True)[:16]:print('RESULT',k,{z:v[z] for z in ['windows','trades','wins','losses','net','participation','drawdown']},flush=True)
if __name__=='__main__':
    import sys
    collect() if 'collect' in sys.argv else main()
