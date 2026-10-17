"""Refresh Dow only; reuse unchanged index signal and portfolio math."""
from common import *
from collections import defaultdict
import analyze_indices as analysis

OLD=P.parent.parent/'kalshi_finance_expansion_2026_10_14'
NOW=int(time.time())
def main():
    target=P/'indices/KXDJI';target.mkdir(parents=True,exist_ok=True)
    original=OLD/'indices/KXDJI'
    previous=read(original/'markets.json')
    fresh=paged('/markets',dict(series_ticker='KXDJI',status='settled',min_close_ts=END,max_close_ts=NOW,limit=1000),'markets')
    markets=sorted({m['ticker']:m for m in previous+fresh if ts(m['close_time'])<NOW and m.get('settlement_ts') and ts(m['settlement_ts'])<NOW}.values(),key=lambda m:m['close_time'])
    save(target/'markets.json',markets);meta=get('/series/KXDJI')['series'];save(target/'series.json',meta)
    events=defaultdict(list);errors=[]
    for m in markets:
        if float(m.get('volume_fp') or 0)>=5:events[m['event_ticker']].append(m)
    for event,items in sorted(events.items()):
        try:
            chart=target/(event+'_chart.json.gz');candles=target/(event+'_candles.json.gz')
            for dest in (chart,candles):
                if not dest.exists() and (original/dest.name).exists():dest.symlink_to(original/dest.name)
            if not chart.exists():save(chart,get('/live_data/events/'+event,{'range':'1h'}))
            if not candles.exists():
                close=ts(items[0]['close_time'])
                response=get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in items),start_ts=close-32*60,end_ts=close,period_interval=1))
                save(candles,{x['market_ticker']:x['candlesticks'] for x in response.get('markets',[])})
                print('NEW',event,flush=True)
        except Exception as exc:errors.append(dict(event=event,error=str(exc)))
    save(target/'receipt.json',dict(events=len(events),errors=errors,asof=NOW))
    analysis.EXT=END
    analysis.main()
    results=read(P/'indices_results.json');pools=read(P/'indices_pools.json.gz');prior=read(OLD/'indices_results.json')
    validation=[]
    for profile in results:
        for scenario in results[profile]:
            rows=[r for r in pools[profile][scenario] if r['asset']=='DJI' and r['close']<END]
            stress=.02 if scenario=='quote2c' else .01
            ledger,_=analysis.e.portfolio(rows,stress);metrics=analysis.e.metrics(ledger)
            before=prior[profile][scenario]['DJI']
            assert all(abs(metrics[k]-before[k])<.0001 for k in ('trades','wins','losses','net','drawdown')),(profile,scenario,metrics,before)
            validation.append(dict(profile=profile,scenario=scenario,previous_reproduced=True))
    save(P/'validation.json',dict(asof=NOW,checks=validation,errors=errors,prior_cutoff=END))
    print('VALIDATED',len(validation),flush=True)
if __name__=='__main__':main()
