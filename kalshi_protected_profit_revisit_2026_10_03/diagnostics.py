"""Attribution and coverage, never parameter selection."""
import collections,json,urllib.request,datetime as dt
import numpy as np
import study as s
from book_replay import entry_signals
P=s.P;x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());ix={m['ticker']:i for i,m in enumerate(mm)}
out={};configs=json.loads((P/'configurations.json').read_text())
for cid in (2,2462,1771,3904):
    c=configs[cid];rows=json.loads((P/f'trades_{cid}.json').read_text());result={}
    for co in s.PERIODS[:4]:
        rr=[r for r in rows if r['cohort']==co];markets=[(i,m) for i,m in enumerate(mm) if m['asset']==c['asset'] and s.PERIODS[s.cohort(m['close'])]==co]
        all_signals={m['ticker']:list(entry_signals(c,x[i],m)) for i,m in markets};entryfees=0.;spreads=0.;recovered=0;gross=0.;fees=0.;benefit=0.;winscut=0;losssaved=0
        for r in rr:
            j=round((r['decision']-mm[ix[r['ticker']]]['start'])/60)-1;ob,oa=x[ix[r['ticker']],j,2:4]
            spreads+=oa-ob;entryfees+=-r['debit']-r['entry'];gross+=r['exit']-r['entry'];fees+=r['exit']-r['entry']-r['pnl']
            recovered+=r['decision']>all_signals[r['ticker']][0]['t'];benefit+=r['pnl']-r['hold_pnl']-.0025*(r['legs']-1)
            if r['legs']==2:
                winscut+=int(r['hold_pnl']>0);losssaved+=int(r['hold_pnl']<0)
        base=s.metrics(rr,len(markets));result[co]=dict(**base,eligible_signal_markets=sum(bool(v) for v in all_signals.values()),recovered_after_first_quote_failure=recovered,gross_price_pnl=gross,total_fees=fees,extra_friction=.0025*sum(r['legs'] for r in rr),exit_change_vs_hold=benefit,early_exits_eventual_winners=winscut,early_exits_eventual_losers=losssaved,entry_fees=entryfees,entry_spreads=spreads,optimistic_free_bid_entry_same_exits_net=base['net']+entryfees+spreads,optimistic_note='Accounting concession only: fee-free purchases at the contemporaneous bid, all historical entries/exits unchanged. NOT a maker backtest or fill/queue evidence.')
    out[str(cid)]=result
s.save('attribution.json',out)
series={}
for a in ('BTC','ETH'):
    url='https://api.elections.kalshi.com/trade-api/v2/series/KX'+a+'15M'
    data=json.load(urllib.request.urlopen(url,timeout=20))['series'];series[a]={k:data.get(k) for k in ('ticker','fee_type','fee_multiplier','frequency')};series[a]['source']=url
s.save('fee_metadata.json',dict(retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),series=series,rounding_source='https://docs.kalshi.com/getting_started/fee_rounding'))
print({k:{co:round(vv['exit_change_vs_hold'],4) for co,vv in v.items()} for k,v in out.items()})
