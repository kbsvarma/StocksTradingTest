"""Reproduce the loss from immutable audit receipts; no network or live writes."""
import gzip
import hashlib
import json
import math
import statistics
from decimal import Decimal as D
from pathlib import Path

P=Path(__file__).resolve().parent
def read(n):return json.loads((P/n).read_text())
order=read('order.json');signal=order['signal'];T=signal['decision_ts']
market=read('market.json')['response']['market']
fills=read('fills_exchange.json')['response']['fills']
settlement=read('settlement_exchange.json')['response']['settlements'][0]
exchange_order=read('order_exchange.json')['response']['order']
checks={}
def check(name,value):
    checks[name]=bool(value)
    assert value,name

bars={};points={}
for file in ('prior_chart.json','chart.json'):
    details=read(file)['response']['live_data']['details']
    check(file+'_asset',details['asset']=='USDCAD')
    for row in details['timeseries']:
        points[row['t']//1000]=float(row['v'])
        if row['t']%60000==59000:bars[row['t']//60000*60]=float(row['v'])
values=[bars[k] for k in range(T-3660,T,60)]
digest=hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()
check('all_61_retrieved_inputs_match_live_hash',digest==signal['underlying_evidence']['values_sha256'])
check('reference_matches_official_strike',bars[signal['underlying_evidence']['reference_ts']]==signal['strike_proxy']==market['floor_strike'])
sigma=statistics.pstdev(math.log(y/x) for x,y in zip(values,values[1:]))
z=-(values[-1]/signal['strike_proxy']-1)/(sigma*math.sqrt(signal['remaining_minutes']))
threshold=max(.5,1.75+.5*math.log2(signal['remaining_minutes']/5))
check('recomputed_sigma',abs(sigma-signal['sigma'])<1e-15)
check('recomputed_z',abs(z-signal['z'])<1e-12)
check('intended_adaptive_gate',abs(threshold-signal['required_z'])<1e-12 and z>=threshold)
check('source_completed_before_decision',signal['underlying_evidence']['latest_completed_second']==T-1)
check('time_gate',1<=signal['remaining_minutes']<=10 and order['close_ts']-T==60)
check('signal_price_spread',.85<=signal['signal_ask']<.95 and signal['signal_ask']-signal['signal_bid']<=.020000001)
candle=next(x for x in read('candles.json')['response']['candlesticks'] if x['end_period_ts']==T)
check('quote_matches_retrieved_candle',abs(1-float(candle['yes_bid']['close_dollars'])-signal['signal_ask'])<1e-12 and abs(1-float(candle['yes_ask']['close_dollars'])-signal['signal_bid'])<1e-12)
check('one_attempt_one_full_fill',len(read('attempts.json'))==1 and len(fills)==1 and D(fills[0]['count_fp'])==5 and D(exchange_order['remaining_count_fp'])==0)
check('correct_NO_side',fills[0]['outcome_side']=='no' and settlement['no_count_fp']=='5.00' and settlement['yes_count_fp']=='0.00')
check('price_limit',D(fills[0]['no_price_dollars'])<=D(order['favorite_limit'])==D('.89')<=D('.939'))
check('depth',signal['book_available_count']==5 and signal['book_worst_ask']<=float(order['favorite_limit']))
check('terminal_confirmed',exchange_order['status']=='executed' and order['terminal_fill_verified']['filled']=='5.00')
cost=sum(D(f['no_price_dollars'])*D(f['count_fp']) for f in fills)
fees=sum(D(f['fee_cost']) for f in fills)
payout=D(settlement['revenue'])/100
net=payout-cost-fees
check('exchange_accounting',cost==D(order['cost'])==D(settlement['no_total_cost_dollars']) and fees==D(order['fees'])==D(settlement['fee_cost']) and net==D(order['pnl']))
check('tie_resolves_YES',market['strike_type']=='greater_or_equal' and D(market['expiration_value'])==D(str(market['floor_strike'])) and market['result']==settlement['market_result']=='yes')
check('chart_final_matches_official',D(str(points[order['close_ts']-1]))==D(market['expiration_value']))
for field in ('hashes','flags','services'):
    check('unchanged_'+field,read('preservation_before.json')[field]==read('preservation_after.json')[field])
for name in ('lanes.py','pyth_data.py','sizing.py','price_rules.py'):
    expected=P.parent/'kalshi_usdcad_2026_10_14/candidate'/name
    check('matches_authorized_release_'+name,(P/'source'/name).read_bytes()==expected.read_bytes())

hist=json.load(gzip.open(P.parent/'kalshi_finance_expansion_2026_10_14/fx/ledgers.json.gz'))['eurusd_adaptive']['quote1c']['USDCAD']['full']
check('historical_68_67_1',len(hist)==68 and sum(r['won'] for r in hist)==67)
check('historical_net',abs(sum(r['net'] for r in hist)-8.191)<1e-9)
check('live_trade_after_research',T>max(r['close'] for r in hist))
out=dict(ticker=order['ticker'],quantity=5,side='no',entry=str(cost/5),fees=str(fees),net=str(net),
    source_hash_matches=True,z=z,required_z=threshold,spot=values[-1],strike=market['floor_strike'],
    cushion_pips=round((market['floor_strike']-values[-1])*10000,6),
    sigma_pips=round(sigma*market['floor_strike']*10000,6),
    qualification_to_submission_ms=1000*(signal['execution_submit_ts']-signal['signal_qualified_ts']),
    submission_to_ack_ms=1000*(signal['execution_ack_ts']-signal['execution_submit_ts']),
    source_receipt_after_terminal_second_ms=1000*(signal['underlying_evidence']['received_ts']-(T-1)),
    roundtrip_to_market_receipt_ms=1000*(signal['execution_ack_ts']-T),
    historical=dict(trades=len(hist),wins=67,losses=1,net=round(sum(r['net'] for r in hist),4),
        minimum_z=min(r['z'] for r in hist),entries_below_live_z=sum(r['z']<=z for r in hist),
        one_minute_entries=sum(r['rem']==1 for r in hist),one_minute_losses=sum(r['rem']==1 and not r['won'] for r in hist)),
    illustrative_gaussian_probability=.5*(1+math.erf(z/math.sqrt(2))),
    actual_cost_breakeven=float((cost+fees)/5),
    probability_warning='Gaussian diagnostic only, not a calibrated win probability; historical entries are quote proxies, not live fills.',
    checks=checks)
(P/'verification.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
