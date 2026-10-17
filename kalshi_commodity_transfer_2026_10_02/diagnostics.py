"""Predeclared robustness, legacy control and portfolio attribution checks."""
import gzip
import hashlib
import json
import math
import pickle
import random
import statistics
from collections import defaultdict
import replay as r

def legacy_gold():
    old = r.ROOT/'kalshi_commodity_validation_2026_09_25'
    markets = [json.loads(line) for line in (old/'data/markets_GOLD.jsonl').read_text().splitlines()]
    candles = {x['ticker']:{c['t']:c for c in x['candles']}
               for x in map(json.loads,(old/'data/candles_GOLD.jsonl').read_text().splitlines())}
    entries = []
    for m in sorted(markets,key=lambda m:m['close_time']):
        close = r.ts(m['close_time'])
        for rem in range(10,2,-1):
            T = close-rem*60
            q = r.quote(candles[m['ticker']].get(T))
            if q is None:
                continue
            side = 1 if sum(q)>1 else -1
            bid,ask = q if side==1 else (1-q[1],1-q[0])
            if not .85-1e-8 <= ask < .93-1e-8 or ask-bid>.02000001:
                continue
            nxt = r.quote(candles[m['ticker']].get(T+60),'o',side)
            if nxt is None:
                continue
            cap = float(r.limit_price(dict(lane=r.Z2,signal_ask=ask,
                        side='yes' if side==1 else 'no',price_ranges=m['price_ranges']))[1])
            price = nxt[1]
            if price<.85-1e-8 or price>cap+1e-8:
                continue
            # Reproduce the prior fee convention exactly for this regression.
            fee = math.ceil((.07*2*price*(1-price)-1e-12)*10000)/10000
            won = m['result']==('yes' if side==1 else 'no')
            entries.append(dict(ticker=m['ticker'],decision_ts=T,net=2*(won-price)-fee-.02))
            break
    saved = json.loads((old/'baseline_trades_GOLD.json').read_text())
    assert [(x['ticker'],x['decision_ts']) for x in entries]==[(x['ticker'],x['decision_ts']) for x in saved]
    total = sum(x['net'] for x in entries)
    assert abs(total-2.5735)<1e-7
    return dict(trades=len(entries),net_fees_plus1c_at_two_contracts=round(total,4),
                exact_old_market_and_decision_match=True)

def boot(rows, start, end):
    days = [r.utcday(t) for t in range(start,end,86400)]
    pnl = defaultdict(float)
    for row in rows:
        pnl[r.utcday(row['close'])] += row['net']
    values = [pnl[d] for d in days]
    rand = random.Random(20261003)
    samples = sorted(sum(rand.choices(values,k=len(values))) for _ in range(20000))
    return {'lower_95_total':round(samples[500],4),'upper_95_total':round(samples[19499],4),
            'resamples':20000,'unit':'UTC market-close day; whole days including no-trade days',
            'limitation':'Exploratory independent-day bootstrap, not selection-adjusted or a future profit guarantee.'}

def main():
    markets = pickle.loads((r.P/'features.pickle').read_bytes())
    with gzip.open(r.P/'trade_ledgers.json.gz','rt') as f:
        ledgers = json.load(f)
    out = {'old_gold_control':legacy_gold(),'uncertainty':{}}
    for period,start,end in r.windows():
        out['uncertainty'][period] = {a:boot(ledgers[f'opening_plus1c/{period}/{a}'],start,end) for a in r.ASSETS}
    # Gold+copper is a reported post-result grouping, not a newly validated rule.
    out['gold_copper_post_result_grouping'] = {}
    for mode, stress in [('opening',.01),('opening',.02),('delay60',.01)]:
        rows,_ = r.opportunities([m for m in markets if m['asset'] in ('GOLD','COPPER')],mode)
        item = {}
        for period,start,end in r.windows():
            trades, blocks = r.portfolio([x for x in rows if start<=x['close']<end],stress)
            item[period] = dict(r.metrics(trades),blocked=blocks)
        out['gold_copper_post_result_grouping'][f'{mode}/{stress}'] = item
    # Diagnose suspicious terminal charts without cherry-picking their outcomes.
    bad = {x['ticker'] for x in json.loads((r.P/'integrity_checks.json').read_text())['mismatches']}
    out['terminal_chart_exceptions'] = {'tickers':sorted(bad),
        'in_primary_z_trades':sum(x['ticker'] in bad for x in ledgers['opening_plus1c/pooled/ALL']),
        'rule':'Actual exchange outcomes are used; exceptions were not deleted from the opportunity universe.'}
    # Compare price proxies over EXACTLY the observation window of archived quotes.
    obs = json.loads((r.P/'recorded_quote_results.json').read_text())['coverage']
    end = obs['last_ts']
    rows,_ = r.opportunities([m for m in markets if m['asset']=='GOLD'])
    matched,_ = r.portfolio([x for x in rows if x['entered']<=end])
    out['gold_candle_proxy_over_archived_quote_window'] = dict(r.metrics(matched),last_quote_ts=end)
    crypto = pickle.loads((r.ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    cr,_ = r.opportunities([dict(m,multiplier=1) for m in crypto if r.START<=m['close']<r.END])
    cm,_ = r.opportunities([m for m in markets if m['asset'] in ('GOLD','COPPER')])
    base,_ = r.portfolio(cr)
    joint,blocks = r.portfolio(cr+cm)
    old_ids = {x['ticker'] for x in base}
    added = [x for x in joint if x['asset'] in ('GOLD','COPPER')]
    out['crypto_plus_gold_copper_post_result_grouping'] = dict(
        baseline=r.metrics(base),joint=r.metrics(joint),commodity_attribution=r.metrics(added),
        net_additional_trades=len(joint)-len(base),incremental_net=round(sum(x['net'] for x in joint)-sum(x['net'] for x in base),5),
        displaced_crypto=len(old_ids-{x['ticker'] for x in joint}),blocked=blocks)
    r.save('diagnostics.json',out)
    print(json.dumps({'legacy':out['old_gold_control'],
          'pooled_uncertainty':out['uncertainty']['pooled'],
          'gold_proxy_matched':{k:out['gold_candle_proxy_over_archived_quote_window'][k] for k in ('trades','wins','losses','net')}}))

if __name__=='__main__':
    main()
