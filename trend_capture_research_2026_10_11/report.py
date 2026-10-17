from pathlib import Path
import pandas as pd,json
P=Path(__file__).resolve().parent;Q=P/'portfolio'
c=pd.read_csv(Q/'controls.csv');s=pd.read_csv(Q/'scouts.csv');g=pd.read_csv(P/'early_gate.csv')
def tab(d,index,col):return d.pivot_table(index=index,columns=col,values='net').round(2).to_string()
report="""TREND CAPTURE RESEARCH — 11 OCTOBER 2026

A useful direction emerged: hold a small long position during a positive slow trend, and otherwise hold cash. Faster entries and more elaborate trailing exits did not improve consistently.

The candidate, LT30, buys when the completed daily close is above the close 30 calendar days earlier. It stays invested until that condition reverses. It uses BTC, ETH, SOL and ZEC with equal allocations, targeting at most 50% of total equity across the four assets. Positions are integer contracts and are rebalanced at the next day's opening quote. There is no intraday profit target or stop.

All portfolio figures below use ONE $100 starting account. The earlier stop-based strategies' $2 planned trade risk does not apply to LT30. Removing that stop permits larger losses within a trade. Gross exposure can drift between daily rebalances.

MAIN RESULTS — net dollar profit after modeled costs
"""
report+=tab(c[c.design.isin(['mom30','always_long'])],['source','scenario','design'],'period')+"""

“proxy” is Coinbase underlying price history for 2022–2025, with assumed perpetual fees and funding. It is not historical Kalshi execution.
“venue” is the archived 2026 Kalshi quote/funding replay, June 3 through October 9 opening quotes. Signal warm-up delays the first trade.
“base” uses 12 bp fees per side plus 1 bp friction. Historical funding is an assumed adverse 1 bp per 8 hours.
“combined” uses 5 bp friction and a one-hour execution delay; historical adverse funding rises to 3 bp per 8 hours. Recent funding uses the archived observations.
“always_long” is a daily-rebalanced basket with the same 50% target allocation and matched 31-day asset warm-up. It is not static buy-and-hold.
“all” is a continuous account. Individual years and early/later windows each reset to $100. Do not add the reset returns together.

WHAT THE NUMBERS SUPPORT

• LT30's continuous 2022–2025 profit was $99.78 base and $71.09 under combined stress.
• Its base annual reset profits were -$10.35, +$35.26, +$20.06 and +$35.25.
• The 2022 loss was much smaller than the always-long comparator's $35.87 loss; under stress the comparison is -$12.12 versus -$43.12.
• Continuous dollar drawdown was $23.02 base and $21.42 under stress. Percentage peak-to-trough drawdown was 16.47% and 17.64%, respectively. These are DAILY sampled drawdowns; intraday losses could be larger.
• Only 24 of 48 historical months were positive under base assumptions, and 22 under stress. This does not support steady weekly income.
• Recent continuous profit was $16.38 base / $16.53 stress. Always-long earned more: $20.50 / $20.18.
• In September 1–October 9, LT30 and always-long held the same exposure and both earned $4.46 base / $4.26 stress. There is no selection advantage in that window.

WHY THIS IS A CANDIDATE, NOT A PROMOTED EDGE

The clearer benefit is reducing exposure during prolonged declines. There is not evidence here of reliably identifying the start of a move.

A particularly important control removes funding from BOTH strategies. At base friction, always-long then earns $128.50 over four years versus LT30's $120.37. With assumed funding included, LT30 earns $99.78 versus $92.65. Thus lower funding exposure can explain its profit advantage over always-long; higher relative advantage under funding stress is not proof of predictive quality. Capital paths compound, so direct subtraction of the two final totals is not a clean causal decomposition.

All periods have now been examined repeatedly. These are exploratory results, not an untouched out-of-sample validation. Choosing four surviving assets with available history also introduces universe-selection risk. Exact historical funding, order-book depth, executable size, intraday drawdown and live fill behavior remain unvalidated.

DEPENDENCE ON ASSETS AND LARGE DAYS

The following reruns remove one asset and redistribute the same total allocation among the remaining three:
"""
report+=c[(c.period=='all')&(c.scenario=='combined')][['source','design','net','dd']].round(2).to_string(index=False)+"""

All four historical contributions to LT30 were positive under stress, but SOL and ZEC supplied most profit; BTC contributed only $0.81 of $71.09. Removing any one asset left a positive basket in both data sets.

Removing the five best daily P&L observations leaves historical stress profit of $34.98. Recent stress profit falls to only $0.56. This is a descriptive concentration calculation, not a tradable modified strategy.

Two hundred joint circular shifts of the timing signals produced equal-or-better stress profit in 2 historical runs and 4 recent runs. This is only a descriptive timing control: the warm-up mask, position rounding and capital paths change turnover. These frequencies are NOT valid post-selection p-values.

BUILDING ON THE LEAD

I tested two additions with their rules written down before running them:
1. A 25% starter allocation when the 7-day trend is positive but the 30-day trend is not; full allocation when the slow trend confirms.
2. Require both 7-day and 30-day trends to be positive for entry, then keep holding until the 30-day trend turns negative.

Their shared-account results:
"""
report+=tab(s[s.design.isin(['mom30','scout25','confirmed_entry'])],['source','scenario','design'],'period')+"""

Neither addition improves consistently. The scout changes historical base profit by only +$0.21 and reduces stress profit by $4.46. Fast confirmation slightly improves recent profit but worsens the historical result. Keep the simpler candidate; these results do not justify tuning the scout size or confirmation threshold.

HOW THE RESEARCH REACHED THIS POINT

Stage 1 tested four hourly entries—6-hour breakout, compressed-volatility 24-hour breakout, EMA 8/32 crossover, and 12-hour momentum—with four exits: fixed 3R, immediate 1R trailing, delayed 1R trailing and a 24-hour channel exit. These ran across all 19 archived assets under four execution scenarios.

Stage 2 transferred those same families to four years of BTC, ETH, SOL and ZEC hourly history.

Stage 3 tested slower momentum, weekly breakout and alignment with the weekly trend, including wider 2R and 3R trails. Wider trails did not produce reliable transfer across the recent and older samples.

Stage 4 changed the mechanism: eight daily allocation designs, using 7/30/90-day momentum or EMA 7/28, each long/flat and long/short. Slow long/flat was the useful branch. Shorting did not consistently improve it.

Stage 5 tested the two early-entry additions above and the no-funding controls.

None of the Stage 1 designs passed its original combined early-period breadth, trade-count and execution-stress gate. The later LT30 candidate belongs to a different risk model; it has not “passed” that stopped-strategy protocol.

COVERAGE AND VALIDATION LIMITS

The weekly breakout produced no recent trades because its strict 168-hour complete-window requirement could not be met with the available hourly quote samples. Treat that as missing evidence, not proof the design fails. Several noncrypto assets have only days of history. No-trade assets are not confirmations.

A separate Coinbase daily-price cross-check found recent opening quotes within 0.05% for BTC, 0.04% for ETH, 0.15% for SOL and 0.25% for ZEC at the sampled overlapping dates. This helps rule out a large price-scale error; it does not establish executable depth. Raw responses and daily basis comparisons are saved.

Historical input contains 35,064 scheduled hourly observations per asset, with 8 missing BTC hours, 8 ETH, 10 SOL and 10 ZEC. No paid Databento data was purchased. Missing hourly prices were not forward-filled into signals.

Stop-based venue replays use next-minute quotes, adverse stop ordering and actual funding. Their combined stress delays execution by two minutes and prevents favorable rebound stop fills. The daily portfolio stress uses a one-hour delay. These different experiments must not be conflated.

The portfolio has zero missing held daily execution observations in the base replay and one in the recent stress replay. That missed execution leaves the old position in place and marks it at the last known quote until a new quote arrives. No portfolio run required a stale terminal liquidation. Daily risk statistics still miss intraday excursions.

The hourly proxy uses OHLC assumptions rather than a historical order book. Current archived fee rates are applied retrospectively, not a verified historical fee schedule. Actual maker fills and trading capacity have not been assumed.

Checks cover causal features, trailing stops applied only to future bars, adverse ordering when stop and target both occur, rejection of price extrema preceding entry, cash/P&L reconciliation, integer quantities, shared-capital fees/funding and zero-exposure behavior. Source dependencies, data, protocols and detailed outputs are preserved.

DECISION

Retain LT30 as a frozen research candidate and compare it with the always-long allocation in future unseen data. Its purpose is participating in long trends while reducing prolonged-decline exposure. The evidence does not support a new early-detection advantage, dependable monthly income or immediate promotion.

candidate.json records the exact research design. No live bot, saved stable checkpoint, service or paper observer was changed.

Reproduction: RUN.txt. Full tables and ledgers are included.

Primary sources checked October 11, 2026:
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
https://help.kalshi.com/en/articles/16071417-perps-fees-explained
"""
(P/'REPORT.txt').write_text(report)
print('Readable report saved')
