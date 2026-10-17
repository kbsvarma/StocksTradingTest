"""Current listings and trailing-week activity for every recurring finance series."""
from common import *
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter,defaultdict
import statistics

def scan(s):
    tick=s['ticker'];p=P/'inventory'/tick;p.mkdir(parents=True,exist_ok=True)
    recent=paged('/markets',dict(series_ticker=tick,status='settled',min_close_ts=END-7*86400,max_close_ts=END,limit=1000),'markets')
    opened=paged('/markets',dict(series_ticker=tick,status='open',limit=1000),'markets')
    save(p/'recent.json',recent);save(p/'open.json',opened)
    events=defaultdict(list)
    for m in recent:events[m['event_ticker']].append(m)
    vols=[sum(float(m.get('volume_fp') or 0) for m in ms) for ms in events.values()]
    out=dict(series=tick,title=s['title'],frequency=s.get('frequency'),open_markets=len(opened),open_events=len({m['event_ticker'] for m in opened}),recent_markets=len(recent),recent_expiries=len(events),positive_volume_expiries=sum(v>0 for v in vols),contracts_7d=sum(vols),median_contracts_expiry=statistics.median(vols) if vols else 0,source=s.get('settlement_sources'),fee_multiplier=s.get('fee_multiplier'),fee_type=s.get('fee_type'))
    save(p/'summary.json',out);print(tick,out['open_events'],out['recent_expiries'],round(out['contracts_7d']),flush=True);return out

if __name__=='__main__':
    series=read(P/'data/series.json')['series']
    selected=[s for s in series if s.get('category')=='Financials' and s.get('frequency') in ('hourly','daily','fifteen_min')]
    save(P/'inventory_spec.json',dict(selected=[s['ticker'] for s in selected],cutoff=END,trailing_days=7))
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(scan,s):s for s in selected}
        for f in as_completed(futures):
            try:results.append(f.result())
            except Exception as e:results.append(dict(series=futures[f]['ticker'],error=str(e)))
    save(P/'inventory.json',dict(captured_end=time.time(),catalogue_financial_definitions=sum(s.get('category')=='Financials' for s in series),recurring_series=len(selected),rows=results))
