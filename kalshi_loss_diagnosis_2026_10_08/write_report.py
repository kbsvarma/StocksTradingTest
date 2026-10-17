import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
read=lambda n:json.loads((P/n).read_text())
a=read('actual_results.json');h=read('historical_results.json');c=read('continuation_results.json');r=read('snapshot_receipt.json');o=read('source_recovery.json');v=read('preservation.json')
time=datetime.fromtimestamp(r['ts'],ZoneInfo('America/New_York')).isoformat()
lines=[f'KALSHI PERFORMANCE AND LOSS DIAGNOSIS — snapshot {time}',
'Production remains LIVE; no entry rules, size, limits, code, funds or services changed.',
'',
'RECOMMENDATION',
'Keep the current entry thresholds. My preferred risk improvement is a maximum of TWO same-direction crypto positions with the same expiry, retaining chronological production priority. This caps one common crypto reversal at two five-contract positions instead of four. It is a concentration control, NOT a demonstrated improvement in expected profit. The earlier replay gives up $2.6590 over ten days and does not improve measured drawdown. Therefore do not present the favorable recent ledger pruning as a validated new edge. No live change was made.',
'Raising crypto z to 2.5, changing Copper solely because its first loss was near z1.5, or favoring premium merely because it has no losses are not supported by the comparisons below.',
'',
'ACTUAL PERFORMANCE (exchange fees included; deposits excluded)',
'Period | Settled | Wins | Losses | Net',]
for label,key in [('Lifetime, mixed historical sizes','lifetime_mixed_sizes'),('Since Oct3 02:16 ET premium activation, sizes actually filled','since_z3_oct3'),('Oct8 midnight ET through snapshot, all 5 contracts','today')]:
 x=a[key]['total'];lines.append(f"{label} | {x['trades']} | {x['wins']} | {x['losses']} | ${x['net']:+.4f}")
lines+=['','TODAY BY MARKET','Market | Trades | Wins | Losses | Net']
for asset in ('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI'):
 x=a['today']['by_asset'][asset];lines.append(f"{asset} | {x['trades']} | {x['wins']} | {x['losses']} | ${x['net']:+.4f}")
lines+=['','Crypto: 10 trades, 7W/3L, -$11.2360. Four new markets: 40 trades, 38W/2L, +$5.2819. New markets have less than one full day of live evidence; no assertion that their win rates are established.',
'Today original lane: 43 trades, 38W/5L, -$7.1916. Premium lane: 7W/0L, +$1.2375. Premium lifetime: 18W/0L, +$2.9807; too few observations to call loss-free or justify increasing its risk.',
'Today 90% wins was insufficient: average winning trade +$0.3739; average losing trade -$4.5562. All-in average spend was 92.38164 cents per contract, implying about 92.38% realized winning contracts to break even for this fixed set of five-contract entries. One loss erases about 12.2 average wins. Premium average spend is 96.6881 cents, making its profit margin even thinner.',
'Lifetime drawdown by market-close order: $19.1429. Mixed historical sizing makes lifetime win percentage alone misleading.',
'',
'LOSS CLUSTER AND EXACT RECONCILIATION',
'At 00:21 ET Oct8, all four crypto markets were bought NO for the 00:30 expiry. BTC lost $4.5599, ETH $4.6418, SOL $4.2947; XRP won $0.3366. Three simultaneous losses total $13.4964. The whole four-position window net was -$13.1598.',
'At Oct5 20:30 expiry, BTC and SOL also lost together (-$9.0630). These are repeated common-direction exposures, not eight independent market risks.',
'All thirteen ledger losses are original-lane trades. SOL Oct8 lost with z=3.1684; a high cushion is not a guaranteed outcome. Copper Oct8 lost at z1.5115 with three minutes left; WTI lost at z2.2509 with ten minutes left.',
'Authenticated GET order/fills/market responses independently verified all FIVE Oct8 losses. Decimal sums of fill quantity, paid price and charged fees exactly equal ledger quantity/cost/fees/P&L; outcomes match official finalized results. Every one filled five contracts. All 255 settled ledger records also satisfy payout minus cost minus fees exactly.',
'',
'ACTUAL LEDGER PRUNING — NOT A FULL REDESIGN REPLAY',
'Keeping at most two same-direction crypto bets per expiry would remove SOL loss and XRP win at 00:30: skip one loss AND one winner. Today 48 trades /44W/4L, -$1.9960 instead of -$5.9541 (+$3.9581).',
'Since premium activation: 130 trades /121W/9L, +$0.7184 instead of -$3.2397. Lifetime mixed sizes: remove seven entries (six winners, one loser), +$11.3208 instead of +$7.9354.',
'A stricter one-position cap would remove more correlated losses but also many more winners. Recorded-order pruning does not insert replacement opportunities or model additional trading after freeing risk/cash. Do not call these figures achievable counterfactual profits.',
'',
'FULL CANDIDATE-MINUTE / PORTFOLIO REPLAY',
'Close-time window Sep23 00:00 UTC through Oct3 00:00 UTC, ten days with all eight markets covered. Current frozen profiles; five contracts; shared25/$25/$20 including open risk; $50 starting cash per route; production asset/lane priority and settlement/cash accounting. A rejected early signal may qualify later. Both lanes share market ownership. No future winner ranking.',
'Historical fees plus 1 cent per contract EXTRA cost; separate 2-cent and execution-cap-plus-1-cent stress cases. Quotes are execution proxies, not proof of five contracts actually filling. These inputs were previously researched and used in selection, not new independent holdout evidence.',
'Candidate | Trades | W/L | Net +1c | Drawdown | Net +2c | Net cap+1c',]
for name in h['matched10d']['fees1c']:
 x=h['matched10d']['fees1c'][name];two=h['matched10d']['fees2c'][name];cap=h['matched10d']['cap1c'][name]
 lines.append(f"{name} | {x['trades']} | {x['wins']}/{x['losses']} | ${x['net']:+.4f} | ${x['settled_drawdown']:.4f} | ${two['net']:+.4f} | ${cap['net']:+.4f}")
lines+=['','THRESHOLD OVERFITTING CHECK',
'Crypto minimum z2.5 looks attractive if one deletes recent executed orders: since Oct3 it removes seven losers and 61 winners, improving recorded subset net to +$6.9368. But the full earlier candidate-minute replay falls from +$58.9151 to +$38.7528 and drawdown rises $14.7583 to $19.2567. Lifetime executed-order pruning also falls from +$7.9354 to +$7.2931. Do not deploy this based on recent losers.',
'Copper z1.75 avoids its one observed live loss while deleting five winners, but costs $12.7011 in the matched full replay. Copper z2 cuts modeled drawdown yet still lowers full-period profit. Twelve live Copper trades cannot select a new parameter reliably.',
'WTI z2 improves matched higher-cost performance and drawdown, but loses $9.2478 of net in the later noncrypto-only Oct3–7 replay and removes ten actual winners while retaining today\'s WTI loss. WTI z2.5 removes sixteen actual winners to avoid one loser, lowering today\'s actual subset net. Do not rush either change.',
'',
'SECOND PASS: PRIOR COMPLETED-MINUTE CONFIRMATION',
'Tested no move against the selected side; no decrease in side-adjusted z; and prior-minute same-side z qualification. Each tested for crypto only and all markets, with exact T-60 feature lookup, no future bars or filling missing inputs. Protocol explicitly exploratory after the first pass.',
'Candidate | Trades | W/L | Net +1c | Drawdown | Net +2c | Net cap+1c',]
for name in c['matched10d']['fees1c']:
 x=c['matched10d']['fees1c'][name];two=c['matched10d']['fees2c'][name];cap=c['matched10d']['cap1c'][name]
 lines.append(f"{name} | {x['trades']} | {x['wins']}/{x['losses']} | ${x['net']:+.4f} | ${x['settled_drawdown']:.4f} | ${two['net']:+.4f} | ${cap['net']:+.4f}")
lines+=['','None of the 19 nonbaseline variants consistently improved net across periods and cost cases. Requiring persistent z across all markets cuts modeled losses 18->6 but trades490->231 and net$58.9151->$39.9209: substantial frequency/profit sacrificed. Loss reduction alone is not the objective.',
'The first/second five-day blocks and broader Sep11–Oct2 available-asset history are retained in the JSON outputs. Later Oct3–7 data is NONCRYPTO ONLY: no full cached crypto price replay after Oct2 was available in these inputs. Recent crypto conclusions therefore use actual execution/recorded signals, not a claimed later full-design replay.',
'Baseline simulator entries, economics and blocker counts exactly reproduced the existing independent budget replay in all fifteen period/cost combinations. Matched ten-day control490/472W18L/+$58.9151 reproduced exactly. Feature-to-pool z alignment checked for all candidate minutes in the second pass.',
'',
'OPERATIONS',
'At snapshot: all three Kalshi services active; unchanged trader PID3738853; zero automatic restarts; fresh heartbeat; LIVE with no halt/error. No outstanding resting order and no nonzero exchange positions. Cash route0$105.5759 and route2$45.3141, both within existing Predictions account. No transfer needed for current $25 shared exposure.',
'All fifty Oct8 settled entries filled five contracts. Losses are confirmed market outcomes, not unfilled orders incorrectly booked as losses.',
'Across 21,168 unique asset/lane/minute decisions today, nineteen rate-limit errors recovered within their decision minutes. Two source-incomplete minutes exhausted retries (EURUSD05:09 ET; Copper18:25 ET). These remain a data-availability gap; no evidence they caused the settled losses and no fabricated z/fill inference for those minutes.',
'Public-request pacing/caching is the next operational improvement to validate, preserving complete-source/freshness and buy caps. The current scoped request timeout is not a request-rate limiter. Recovery already handled all nineteen observed429 minutes, so do not promise extra profitable fills from this change.',
'Seven final eligible asset/lane/minute decisions were blocked by the daily loss budget including open risk. They are not seven proven missed profitable trades. Do not increase that budget merely to bypass the day\'s losses.',
'',
'PRESERVATION AND EVIDENCE',
'22 production Python files and12 stable-checkpoint hashes preserved; all service PIDs unchanged; shared25/$25/$20 unchanged. No live mutations, orders or funds transfers. Source and baseline verification in preservation.json.',
'Local and server research folder: kalshi_loss_diagnosis_2026_10_08. Server-only consistent SQLite audit snapshot retained, never restored over live data. orders.json.gz, attempts.json.gz, status_snapshot.json, exchange_loss_proof.json, actual_results.json, historical_results.json, historical_entries.json.gz, continuation_results.json, source_recovery.json, loss_signal_traces.json contain supporting inputs/results.',
'Reproduce on server: /home/varma/kalshi/venv/bin/python /home/varma/kalshi_loss_diagnosis_2026_10_08/analyze.py then continuation.py. Prior research pools/features remain at their referenced sibling paths. snapshot.py is read-only but creates a fresh dated audit snapshot if rerun; preserve this audit before taking a new snapshot.',
]
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(time,'report',len(lines),'lines')
