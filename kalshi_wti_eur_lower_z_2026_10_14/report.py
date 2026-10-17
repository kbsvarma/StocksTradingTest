"""Render the saved research results without rerunning or selecting a grid."""
import json,gzip
from pathlib import Path
import replay as r
P=r.P
R=json.loads((P/'results.json').read_text());A=json.loads((P/'independent_audit.json').read_text());C=json.loads((P/'coverage.json').read_text())
with gzip.open(P/'ledgers.json.gz','rt') as f:L=json.load(f)
lines=[]
def add(s=''):lines.append(s)
def pair(asset,name):
 delta,both=r.CONFIGS[name];o=1.5 if asset=='WTI' else 1.75;p=2.5 if asset=='WTI' else 2.75
 return f'{o-delta:.2f}/{p-(delta if both else 0):.2f}'
add('WTI / EURUSD: modest lower-z full-input replay — 2026-10-14')
add('Live bot unchanged. Offline research; no orders, live edits, restarts, or account actions.')
add()
add('Finding: lowering WTI z increases frequency but degrades the broad-period result. EURUSD has a modest exploratory improvement, especially original/premium base z 1.50/2.50; the incremental confidence interval includes zero, so this is a candidate for further verification, not a proven live edge.')
add('Primary model: 5 contracts per entry, modeled exchange fees plus 1 cent per contract friction; all price/time/risk limits unchanged. Trade counts are simulated fills from candle quotes, not verified five-contract executions.')
add()
for asset in ('WTI','EURUSD'):
 add(asset+' broad available-history comparison')
 add('Period: '+('September 14 00:00 ET–October 14 11:30 ET.' if asset=='WTI' else 'September 23 first listed market (18:15 ET open)–October 14 11:30 ET.'))
 add('z original/premium | Trades | Extra | Wins | Losses (extra) | Net $ | Change $ | Max drawdown $ | Extra/calendar day')
 for name in r.CONFIGS:
  q=R[asset][name]['fees1c']['full'];d=q['comparison'];freq=A['frequency'][asset][name]
  add(f"{pair(asset,name):>17} | {q['trades']:6d} | {d['extra_trades']:+5d} | {q['wins']:4d} | {q['losses']:2d} ({d['extra_losses']:+d}) | {q['net']:+.4f} | {d['net_change']:+.4f} | {q['drawdown']:.4f} | {freq['extra_per_calendar_day']:+.3f}")
 add('EURUSD values are bases at five minutes; retain threshold=max(0.5, base + 0.5*log2(minutes/5)).' if asset=='EURUSD' else 'WTI thresholds remain constant across the existing 3–10 minute window.')
 add()
 add('Earlier vs recent results: no hiding the period where a setting fails')
 add('z original/premium | Before October 8: trades/losses/net | October 8–14 11:30 ET: trades/losses/net')
 for name in r.CONFIGS:
  a=R[asset][name]['fees1c']['earlier'];b=R[asset][name]['fees1c']['recent']
  add(f"{pair(asset,name):>17} | {a['trades']}/{a['losses']}/${a['net']:+.4f} | {b['trades']}/{b['losses']}/${b['net']:+.4f}")
 add()
 add('Cost/execution sensitivity: each case is re-simulated through the same cash/risk rules')
 add('z original/premium | Fees only T/L/net | Fees+2c T/L/net | At buy cap+1c T/L/net | 60-second delayed+1c T/L/net')
 for name in r.CONFIGS:
  cells=[]
  for sc in ('fees_only','fees2c','cap1c','delay60_1c'):
   q=R[asset][name][sc]['full'];cells.append(f"{q['trades']}/{q['losses']}/${q['net']:+.4f}")
  add(pair(asset,name)+' | '+' | '.join(cells))
 add('Fees-only and stressed counts can differ: losses, risk brakes and route cash change subsequent eligibility. Net after fees alone is not the same as net after assumed execution friction.')
 add()
add('What is actually added, removed or entered earlier? (primary full-history model)')
for asset in ('WTI','EURUSD'):
 for name in r.CONFIGS:
  if name=='current':continue
  q=R[asset][name]['fees1c']['full'];d=q['comparison']
  add(f"{asset} {pair(asset,name)}: {d['added']['trades']} newly traded markets, {d['added']['losses']} losses among those, added-market net ${d['added']['net']:+.4f}; {d['removed']['trades']} previously traded markets displaced; {d['retimed']} shared markets change entry timing/lane/side/price. Net trade increase {d['extra_trades']}. Changes in losing trades also include earlier entries changing outcomes, not just losses on new markets.")
add()
add('Combined current eight-asset portfolio, October 8–14 11:30 ET')
add('Apply the current BTC original z3 and USD/CAD rules throughout this historical slice; this is a counterfactual, not a reconstruction of historical live settings. Starting simulated cash $50 per existing route ($100 total), no topups; official settlement delays, all shared 25 contracts / $25 exposure / $20 daily loss and premium 25/$25/$25 limits enforced. This avoids assuming unlimited room for extra trades.')
for name in ('current','wti_original_minus_010','wti_original_minus_025','eurusd_original_minus_010','eurusd_original_minus_025','eurusd_both_minus_010','eurusd_both_minus_025'):
 q=R['portfolio'][name]['fees1c']['recent'];d=q['comparison']
 add(f"{name}: {q['trades']} trades, {q['wins']} wins/{q['losses']} losses, net ${q['net']:+.4f}, drawdown ${q['drawdown']:.4f}; extra {d['extra_trades']} trades, {d['extra_losses']:+d} losses, net delta ${d['net_change']:+.4f}.")
add('EURUSD 1.50/2.50: +13 trades, no additional losses and +$4.2198 in this recent portfolio slice; no other asset trades displaced. Broad standalone gain is +74 trades/+2 losses/+$12.0385, averaging +3.572/calendar day (+4.643 per complete weekday). Recent gain is +2.006/calendar day. Neither rate is a forecast or a live-fill guarantee.')
add()
add('Why the WTI result is not a green light')
add('WTI 1.25/2.50 looks better in just October 8–14 (+$4.3906 versus baseline), but the earlier block falls from +$40.1622 to -$21.2824. Across the full period, losses grow from 22 to 60 and net falls from +$52.8981 to -$4.1559. Lowering premium too does not repair that. The primary replay already permits fresh later opportunities; lack of old filled trades is not being mistaken for lack of eligible signals.')
add()
add('Uncertainty and execution limitations')
for asset,name in [('WTI','original_minus_010'),('WTI','original_minus_025'),('EURUSD','original_minus_025'),('EURUSD','both_minus_025')]:
 lo,hi=A['uncertainty'][asset][name]['incremental_net_day_bootstrap95'];add(f'{asset} {pair(asset,name)} exploratory 95% interval for incremental net: ${lo:+.2f} to ${hi:+.2f}.')
add('Intervals resample 20,000 calendar-day blocks including zero-trade dates. They are not adjusted for extensive previous research, parameter selection, or longer serial dependence. These are reused data, not untouched holdouts. Two extra losses on EURUSD is a small sample; the favorable result does not establish a durable edge.')
add('Normal execution uses the worse of the completed signal ask and next candle opening ask. Both must fit the original tick-rounded buy cap and price floor; no clipping unaffordable quotes to the cap. The 60-second case uses next candle closing ask. Full five-contract depth, queue position, exact feed first-arrival latency, and up-to-three IOC attempt paths are unavailable in these minute candles. Thus this is a strategy/quote replay, not a complete replay of live order microstructure. The very large EURUSD drop in delayed fills (157 to 18 at baseline; 231 to 38 for 1.50/2.50) makes execution verification important.')
add('All z predictors use exactly 61 completed underlying minute endpoints for 60 population log-return observations and the pre-open reference. No future source values, settlement labels or later best prices are used to select signals. Labels are the official market result. Historical snapshots can contain revisions; this run does not pretend they prove contemporaneous availability.')
add('Rounding/fees: archived series multiplier; quadratic taker model 0.07*C*p*(1-p), round model fee upward to 6 decimals and cost+fee debit upward to $0.0001. 1c friction is $0.05 per five-contract trade; it is an additional accounting stress, not permission to exceed the order price limit.')
add('Primary single-asset rows run both lanes with one owner per market, not independent duplicate bets. Eligible signals are replayed minute by minute; reducing z may claim a market earlier at a different price/side, replace premium fills, or trigger daily brakes. Do not estimate new P&L by filtering old filled ledger z values.')
add()
add('Coverage and verification')
for asset in ('WTI','EURUSD'):
 q=C[asset][asset];add(f"{asset}: {q['markets']} markets, {q['feature_minutes']}/{q['expected_minutes']} scheduled feature minutes available; {q['missing_minutes']} missing ({100*q['missing_minutes']/q['expected_minutes']:.2f}%). Missing source/quote periods stay excluded; no interpolation.")
add('WTI uses one archived source before October 8 and the later archived source after it, with no missing join interval. EURUSD uses the complete latest archived FX feature cache. Inputs are hashed. Current EURUSD baseline exactly reproduces previous research: 157 trades /154 wins/3 losses/+34.0230 after fees+1c.')
add('Independent source recomputation: 31,013 decision features; maximum z discrepancy <6e-12. Independent Decimal arithmetic: 53,457 ledger rows; 215 net-change decompositions. All ownership, timing, cap, quantity, cash, contract and risk checks passed.')
add('Separate same-hour receipt check: October 14 14:30–16:00 ET, WTI and EURUSD each had six price-qualified lane-minute checks. None pass even the lowest tested thresholds. These modest changes would not have produced an entry in that quiet interval; the backtest cutoff is earlier (11:30 ET).')
add()
add('Artifacts: PLAN.txt; replay.py; audit.py; input_manifest.json; coverage.json; results.json; ledgers.json.gz; validation.json; independent_audit.json; quiet_window_recheck.json; preservation_before.json / preservation_after.json.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(P/'REPORT.txt')
