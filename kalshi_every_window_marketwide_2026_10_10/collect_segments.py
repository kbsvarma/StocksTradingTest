from common import *
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
SRC=P.parent/'kalshi_nfl_nhl_frequency_2026_10_10'

def main():
    full={}
    for league in ['NFL','NHL']:
        s='KX'+league+'GAME';groups=defaultdict(list)
        for m in read(SRC/'data'/s/'markets.json'):groups[m['event_ticker']].append(m)
        for et,ms in groups.items():
            mi=read(SRC/'data'/s/(et+'_milestone.json'))
            full[et]={'league':league,'markets':ms,'milestone':mi}
    segments=defaultdict(list);reject=[]
    for file in sorted((P/'catalogue').glob('*_markets.json')):
        s=file.name.removesuffix('_markets.json');league='NFL' if 'NFL' in s else 'NHL';per=int(s[5])
        for m in read(file):
            et=m['event_ticker'].replace(s,'KX'+league+'GAME')
            if et not in full:
                reject.append({'ticker':m['ticker'],'reason':'outside_frozen_fullgame_source'});continue
            if full[et]['milestone']['details']['season_type'] not in ('REG','POST'):
                continue
            segments[et].append({'period':per,'market':m})
    def one(et,items):
        info=full[et];mi=info['milestone'];start=ts(mi['start_date'])
        end=min(max(ts(i['market']['close_time']) for i in items)+120,start+12*3600)
        path=P/'candles'/(et+'.json.gz')
        if not path.exists():
            old=SRC/'period_data'/(et+'_candles.json.gz');cm=read(old) if old.exists() else {}
            missing=[i['market']['ticker'] for i in items if i['market']['ticker'] not in cm]
            if missing:
                z=get('/markets/candlesticks',{'market_tickers':','.join(missing),'start_ts':start-3600,'end_ts':end,'period_interval':1})
                cm.update({r['market_ticker']:r['candlesticks'] for r in z.get('markets',[])})
            save(path,cm)
        return {'event':et,'league':info['league'],'milestone':mi,'full_markets':info['markets'],'segments':items}
    out=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(one,et,items):et for et,items in segments.items()}
        for n,f in enumerate(as_completed(futures),1):
            try:out.append(f.result())
            except Exception as ex:errors.append({'event':futures[f],'error':str(ex)})
            if n%25==0:print('FETCHED',n,'of',len(futures),flush=True)
    save(P/'segment_index.json',out);save(P/'collection_gaps.json',{'unmatched':reject,'errors':errors})
    print('DONE',dict(Counter(i['league'] for i in out)),'errors',errors,flush=True)
if __name__=='__main__':main()
