"""Post-entry price lows for the frozen106WTI winners; no exit filters/orders."""
import datetime as dt
import gzip
import hashlib
import json
import pickle
import statistics
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

P=Path(__file__).resolve().parent;R=P.parent;ET=ZoneInfo('America/New_York')
inputs=pickle.load(gzip.open(R/'inputs.pkl.gz','rb'))
orders={o['ticker']:o for o in json.loads((R/'frozen_orders.json').read_text())['orders']}
rows=[]
def n(x):return float(x)
def iso(ts):return dt.datetime.fromtimestamp(ts,ET).isoformat()
for r in inputs['actual']:
    if not r['won']:continue
    o=orders[r['ticker']];assert o['side']==o['result']
    q=D(o['filled']);entry=D(o['cost'])/q
    known=[];inclusive=[];complete=[]
    for end,c in sorted(inputs['markets'][r['ticker']]['candles'].items()):
        if end<=r['entered'] or end>r['close']:continue
        def bid(suffix):
            v=c['b'+suffix] if r['side']==1 else c['a'+{'l':'h','c':'c'}[suffix]]
            assert v is not None
            px=D(str(v)) if r['side']==1 else 1-D(str(v))
            assert 0<=px<=1
            return px
        low=bid('l');close=bid('c')
        inclusive.append(dict(price=low,start=end-60,end=end,kind='minute_low'))
        if end-60>=r['entered']:
            x=dict(price=low,start=end-60,end=end,kind='full_post_entry_minute_low')
            known.append(x);complete.append(x)
        else:
            known.append(dict(price=close,start=end,end=end,kind='post_entry_minute_close'))
    assert complete and known
    low=min(known,key=lambda x:x['price']);inc=min(inclusive,key=lambda x:x['price'])
    mae=max(D(0),entry-low['price']);inclusive_mae=max(D(0),entry-inc['price'])
    row=dict(ticker=r['ticker'],lane=r['lane'],side=o['side'],entry=n(entry),quantity=n(q),hold_net=n(D(o['pnl'])),
      entry_ts=r['entered'],close_et=iso(r['close']),lowest_bid=n(low['price']),low_interval_start_et=iso(low['start']),low_interval_end_et=iso(low['end']),low_kind=low['kind'],
      drawdown_cents=n(mae*100),drawdown_dollars=n(mae*q),drawdown_percent_of_entry=n(mae/entry*100),
      including_ambiguous_entry_minute_low=n(inc['price']),including_ambiguous_entry_minute_drawdown_cents=n(inclusive_mae*100),
      complete_post_entry_minutes=len(complete),quote_points=[dict(x,price=n(x['price'])) for x in known])
    rows.append(row)
assert len(rows)==106 and len({r['ticker'] for r in rows})==106
thresholds={str(c):sum(D(str(r['lowest_bid']))<=D(c)/100 for r in rows) for c in [5,9,10,20,30,40,50,60,70,80,90]}
ambiguous_thresholds={str(c):sum(D(str(r['including_ambiguous_entry_minute_low']))<=D(c)/100 for r in rows) for c in [5,9,10,20,30,40,50,60,70,80,90]}
deep=sorted([r for r in rows if r['lowest_bid']<=.2],key=lambda r:r['lowest_bid'])
assert len(deep)==2
for r in deep:
    raw=json.loads((P/(r['ticker']+'_raw_candles.json')).read_text())['response']['candlesticks']
    prices=[]
    for c in raw:
        if c['end_period_ts']-60<r['entry_ts'] or c['end_period_ts']>orders[r['ticker']]['close_ts']:continue
        v=c['price']['high_dollars'] if r['side']=='no' else c['price']['low_dollars']
        if v is not None:prices.append(1-D(v) if r['side']=='no' else D(v))
    r['lowest_executed_trade_price']=n(min(prices))
    assert r['lowest_executed_trade_price']<=.2
    final=next(c for c in raw if c['end_period_ts']==orders[r['ticker']]['close_ts'])
    assert 1-D(final['yes_ask']['high_dollars'])==D(str(r['lowest_bid']))
    r['last_trade_price_before_close']=n(1-D(final['price']['close_dollars']))
stats={key:{'median':statistics.median(r[key] for r in rows),'mean':statistics.mean(r[key] for r in rows),'max':max(r[key] for r in rows),'min':min(r[key] for r in rows)} for key in ['lowest_bid','drawdown_cents','drawdown_dollars','including_ambiguous_entry_minute_drawdown_cents']}
result=dict(winners=106,threshold_counts=thresholds,threshold_counts_including_ambiguous_entry_minute=ambiguous_thresholds,stats=stats,
    winners_below20=deep,all_winners=sorted(rows,key=lambda r:r['lowest_bid']),
    zero_bid_winners=sum(r['lowest_bid']==0 for r in rows),production_accessed=False,
    source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [R/'inputs.pkl.gz',R/'frozen_orders.json',*P.glob('*_raw_candles.json')]})
(P/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
lines=['WTI106 WINNERS — PRICE DRAWDOWN ONLY','',
 'Yes:2/106winners recorded a held-side bid<=20c after entry. Lowest9.3c and15c; exchange trade-price lows9.6c and15c independently confirm sub20c trades.',
 'No z/time filters, no simulated exits/PnL changes. Drawdown means actual entry price minus lowest recorded sell-side bid, clipped at0; dollars are the mark-to-bid decline for5contracts, before fees. This is maximum adverse excursion from entry, not portfolio peak-to-trough drawdown.',
 'The frozen sample remains Oct8–14,106winning trades from111actual WTI trades;89original and17premium winners.', '',
 'Cumulative thresholds: lowest bid at or below X; a winner can appear in several rows.']
lines += [f'{c}c: {thresholds[str(c)]}/106' for c in [5,9,10,20,30,40,50,60,70,80,90]]
lines+=['','Two deep recoveries:']
for r in deep:lines.append(f"{r['ticker']} {r['side'].upper()} | entry{100*r['entry']:g}c | bid low{100*r['lowest_bid']:g}c | trade low{100*r['lowest_executed_trade_price']:g}c | quote drawdown{r['drawdown_cents']:g}c/contract, ${r['drawdown_dollars']:.3f}/5contracts | interval{r['low_interval_start_et']} to{r['low_interval_end_et']} | final trade{100*r['last_trade_price_before_close']:g}c | won, hold net${r['hold_net']:.4f}")
lines+=['','Post-entry observed summary: '+json.dumps(stats,sort_keys=True),'',
 'Boundary handling: use bid lows only from complete minutes beginning after the actual entry acknowledgement, plus the entry minute closing bid (a known post-entry observation). Do not credit the whole entry-minute low as definitely post-entry. The true minimum during the partial entry minute is unknown. Including its entire low is retained as an ambiguity sensitivity; the<=20c winner count and9.3c deepest low do not change. These are minute quotes, not guarantees all5contracts could sell at that low. Trade-price candles corroborate the two deep drops, but do not supply exit depth.',
 'No winner observed below9.3c in this sample is a historical fact, not a safe future stop level. Both deepest lows occur during the final minute. No production files, state, services or endpoints accessed; local frozen inputs and two public historical GETs only.', '',
 'All106winners sorted by lowest bid:',
 'Market | lane | side | entry(c) | minimum bid(c) | decline(c/contract) | decline($/5contracts) | minimum interval start ET']
for r in result['all_winners']:lines.append(f"{r['ticker']} | {r['lane']} | {r['side']} | {r['entry']*100:.3f} | {r['lowest_bid']*100:.3f} | {r['drawdown_cents']:.3f} | {r['drawdown_dollars']:.4f} | {r['low_interval_start_et']}")
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps(dict(threshold_counts=thresholds,stats=stats,deep=deep),indent=2))
