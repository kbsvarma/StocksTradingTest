"""Offline risk-capacity sweep; no entry rule, size or live setting changes."""
import pickle,json,collections
import study as s
from portfolio import portfolio
from uncertainty import daily,intervals

if __name__=='__main__':
    markets=pickle.loads((s.P/'features.pickle').read_bytes());books=s.old.load_books()
    result={};saved={}
    for scenario,bb,delay in [('quote',None,0),('book',books,0),('book_delay10',books,10),('quote_delay60',None,60)]:
        signals,_=s.old.replay(markets,None,bb,quantity=5,delay=delay,all_signals=True,rules=s.BASE_RULES)
        result[scenario]={};saved[scenario]={}
        for co in ('fit','selection','later','fresh'):
            if bb is not None and co in ('later','fresh'):continue
            rows={k:[r for r in v if s.cohort(r['close'])==co] for k,v in signals.items()}
            days=sorted({s.day(m['close']) for m in markets if s.cohort(m['close'])==co})
            result[scenario][co]={};saved[scenario][co]={};baseline=None
            for budget in (10,12,15,20):
                def replay(stress=.01,cap=False):return portfolio(rows,s.BASE,20,19,budget,stress=stress,cap_cost=cap)
                trades,blocked=replay();cap,_=replay(cap=True);stressed,_=replay(stress=.02)
                if baseline is None:baseline=trades
                summary=dict(s.metrics(trades),blocked=blocked,
                    asset={a:s.metrics([r for r in trades if r['asset']==a]) for a in s.old.ASSETS},
                    limit_price_net=s.metrics(cap)['net'],stress2c_net=s.metrics(stressed,.02)['net'],
                    incremental_trades=len(trades)-len(baseline),incremental_net=round(s.metrics(trades)['net']-s.metrics(baseline)['net'],5),
                    uncertainty=intervals(daily(trades,days)-daily(baseline,days)))
                result[scenario][co][str(budget)]=summary;saved[scenario][co][str(budget)]=trades
                print(scenario,co,budget,len(trades),summary['net'],summary.get('drawdown'),summary['incremental_net'],flush=True)
    s.save('capacity.json',result)
    (s.P/'capacity_trades.pickle').write_bytes(pickle.dumps(saved,protocol=5))
