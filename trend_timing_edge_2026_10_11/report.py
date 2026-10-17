from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
def table(d,idx,col,value='net'):return d.pivot_table(index=idx,columns=col,values=value).round(2).to_string()
a=pd.read_csv(P/'eight_results.csv');r=pd.read_csv(P/'rotation_results.csv');t=pd.read_csv(P/'transfer_results.csv');h=pd.read_csv(P/'hybrid_results.csv');f=pd.read_csv(P/'factor_results.csv');ph=pd.read_csv(P/'phase_results.csv');om=pd.read_csv(P/'eight_omissions.csv');fw=pd.read_csv(P/'forward_summary.csv')
text="""EARLIER TREND CAPTURE AND RELATIVE STRENGTH — 11 OCTOBER 2026

Finding: weekly relative-strength rotation is a stronger research direction than LT30's funding-saving explanation. It improves on equal-weight holding in these samples even when funding is removed from BOTH strategies. The evidence remains exploratory: large drawdowns, concentration, schedule sensitivity and wide uncertainty prevent a claim of durable alpha.

WHAT WAS TESTED

The original 12-candidate grid covered asymmetric early-entry/slow-exit breakouts, recovery after a drawdown, compression breakouts, acceleration, and cross-asset relative strength. Every rule was run with and without funding and under base and stressed execution.

The original selection criterion used 2022–2024 mean annual excess return, taking the worst of four fee/funding scenarios. NONE passed. Daily top-two 7-day momentum was the least-bad selection, not a qualified winner.

I then tested four fixed extensions: weekly selection, a rank buffer to reduce switching, positive-return gating, and a half-sized rotation blend. Weekly selection was the useful branch. Its Monday schedule was declared before seeing its results; all seven weekdays were subsequently checked without choosing the most profitable one.

The locked rules transferred to four additional assets downloaded free from Coinbase: LTC, DOGE, ADA and BCH. Risk-adjusted momentum and a volatility-only comparator were also declared before examining their transfer outcomes. Finally, I tested LT30 hybrids and an eight-asset diversification check.

EXACT WEEKLY RULE

At Monday's completed daily close, rank assets by their prior 7-calendar-day return. Hold the strongest half: two of four, or four of eight. Execute at Tuesday's opening quote. Keep those selected names until the next weekly decision. Daily position sizing maintains equal selected-asset allocations within a target 50% total gross exposure. This is selection once a week, not necessarily only one order a week.

The risk-adjusted version ranks the same 7-day return divided by the prior 20-day daily-return standard deviation times sqrt(7). It attempts to distinguish strength from simply high volatility.

Both versions are long-only, without an intraday stop or profit target. They can hold falling assets if those assets are falling less than the rest. All accounts start at $100. No leverage multiplier was added to manufacture returns.

PRIMARY EIGHT-ASSET CHECK — one shared $100 account, 2022–2025 continuous

Assets: BTC, ETH, SOL, ZEC, LTC, DOGE, ADA, BCH. Uniform fractional underlying units eliminate contract-rounding differences. This is an economic price proxy, not proof these eight instruments or fractional sizes are executable on Kalshi.

Net dollar profit:
"""
text+=table(a[a.period=='all'],['design','funding_enabled'],'scenario')+"""

Interpretation:
• Plain weekly rotation earns $93.85 base versus $44.30 for equal-weight holding.
• With funding removed from both, it earns $140.18 versus $78.81. Its relative advantage therefore cannot be explained solely by avoiding funding.
• Under the combined stress with funding, it earns $19.32 versus a $5.96 loss for holding.
• Risk-adjusted rotation earns $131.31 base and $39.93 stress. This is a later exploratory extension, not a prespecified winning model.
• Base daily-sampled drawdown is 42.58% for plain rotation, 41.49% for risk-adjusted rotation and 44.87% for equal-weight holding. Those are substantial losses, not a stable income design.
• The plain rotation's annual reset profits are -$29.95, +$52.19, +$38.05 and +$32.36. Equal-weight holding performs better in 2024. Annual resets do not add to the continuous return.

ORIGINAL FOUR ASSETS AND TRANSFER TO FOUR ADDITIONAL ASSETS

All figures below are net profits. “proxy” is original BTC/ETH/SOL/ZEC 2022–2025 history. “venue” is the recent archived quote replay, June 3–October 9 opening observations in 2026. “transfer” is the additional LTC/DOGE/ADA/BCH 2022–2025 history.
"""
d=pd.concat([r,t])
text+=table(d[(d.period=='all')&d.design.isin(['always_long','LT30','weekly_top2'])],['source','design','funding_enabled'],'scenario')+"""

Recent original-asset rotation earns $36.30 base / $37.69 stress versus $20.50 / $20.18 for holding. In the latest September–October reset window it earns $11.12 / $10.75 versus $4.46 / $4.26.

On the additional four assets, rotation earns $43.61 base versus $12.46 for holding. Removing funding gives $77.96 versus $39.35. Under high funding stress it still LOSES $9.75, although holding loses $26.81. An advantage relative to holding is not the same as positive absolute performance under every condition.

MINIMUM LOTS AND RISK MATCHING

At $100, contract rounding leaves capital idle: original four-asset historical average gross exposure is about 44% for rotation versus 40% for holding; recent exposure is about 30% versus 25%. This contributes to differences.

The advantage survives a separate fractional-unit check at the same target exposure. Original four-asset history earns $158.17 versus $78.37 base, or $56.78 versus $16.35 stress. Recent fractional replay earns $41.61 versus $26.60 base. These are analytical sensitivities, not promised fractional-contract fills.

The primary benchmark is daily-rebalanced equal-weight long exposure, with the same per-asset 31-day warm-up and 50% total gross target. I also tested genuine fixed-quantity holding. It drifts to much higher exposure; under long historical funding stress its notional can exceed equity, so it is NOT a like-for-like unlevered benchmark. Those outputs are retained separately and should not be used as clean risk-adjusted comparisons.

CONCENTRATION AND ASSET REMOVAL

ZEC contributes $81.67 of plain eight-asset rotation's $93.85 base profit, and $104.83 of the risk-adjusted version's $131.31. Concentration is material.

Every eight-asset leave-one-out rerun still beats its corresponding equal-weight comparator with funding disabled under combined stress. Nevertheless, dropping ZEC makes absolute stressed profits negative. Omitting BCH also makes plain rotation negative under that stress. The rules retain four selections among the remaining seven, with the same total budget.

Stressed profits with funding:
"""
text+=table(om[(om.scenario=='combined')&om.funding_enabled],'omit','design')+"""

SCHEDULE SENSITIVITY

The table gives minimum, median and maximum CONTINUOUS net profit across all seven weekly decision weekdays under combined stress with funding. These are sensitivity runs, not a menu from which a best weekday was chosen.
"""
text+=ph[(ph.period=='all')&ph.funding_enabled&(ph.scenario=='combined')].groupby(['source','design']).net.agg(['min','median','max']).round(2).to_string()+"""

Sunday rotation fails to beat holding in the recent original-asset sample. On the additional assets, most schedules lose money under high funding stress. All original-asset historical schedules beat the holding comparator under this stress, but absolute and annual results vary materially.

IS THERE PREDICTIVE EVIDENCE?

As a diagnostic only, I compared the selected basket's NEXT seven-day gross return with equal-weight holding, using nonoverlapping Tuesday-to-Tuesday observations. This future return is never used in the trading signal.

The uncertainty intervals below come from a four-week block bootstrap and are NOT adjusted for the many designs explored:
"""
text+=fw.round(2).to_string(index=False)+"""

All reported 95% intervals include zero. The full-history average weekly selection advantage is positive in both universes, but the evidence is not strong enough to call it a statistically established forecast edge. The older original sample's pre-2025 mean falls from about 30 bp to 3 bp after excluding its three strongest weeks; the additional assets' pre-2025 mean becomes negative after the same exclusion.

Timing controls circularly shifted the frozen weekly weight sequence in whole weeks, preserving much of its asset preference. With funding removed and execution stressed, equal-or-better results occurred in 6/200 original-history shifts, 10/200 transfer shifts and 2/17 recent shifts. Warm-up masking, rounding and changed capital paths mean these are descriptive controls, not exactly turnover-matched nulls or valid post-selection p-values.

FAILED COMBINATIONS MUST NOT BE HIDDEN

Adding LT30 to weekly selection produces a headline original-history profit of $239.03 base / $160.80 stress, but loses $7.42 / $26.55 on the additional assets. It is rejected as a general improvement. Positive 7-day gating and early breakouts also failed to transfer consistently.

A volatility-only weekly comparator explains much of the original-universe profit. It earns $161.29 base versus plain rotation's $170.54; plain rotation is only modestly better there. On the additional universe, volatility-only earns $3.23 versus rotation's $43.61. Risk-adjusted momentum improves the original sample more, but performs worse than raw momentum in the additional sample. Do not advertise the largest original-sample headline as a universally superior model.

COSTS, DATA AND LIMITATIONS

Historical fees are assumed at 12 bp per side, plus 1 bp friction base / 5 bp stress. Historical funding is an adverse 1 bp per 8 hours base / 3 bp stress. Stress delays the daily execution by one hour. “Funding disabled” retains trading fees and friction; it is an attribution control, not a perpetual trading forecast.

The recent replay uses archived bid/ask quotes and actual funding accumulators, with the archived account fee rates applied retrospectively. Kalshi's official documentation confirms fees apply to both sides on notional and that funding is separate:
https://help.kalshi.com/en/articles/16071417-perps-fees-explained

Historical Coinbase OHLC prices do not provide actual perpetual funding, order-book depth, market impact, borrowing/financing, or executable capacity. New-asset availability on a particular trading venue is not established by these tests. No guaranteed maker fills were assumed. Daily drawdowns omit intraday excursions. Taxes and fixed infrastructure expenses are not modeled.

Data source:
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles

All data and earlier periods have been examined during iterative research. The additional assets are a transfer check with rules fixed before their results; they are still correlated crypto assets and an availability-selected universe, not pristine independent proof. Annual and split runs reset to $100 and must not be added to continuous equity.

DECISION

There is now a concrete research direction beyond funding avoidance: weekly cross-asset relative strength, with risk normalization as a secondary candidate. Preserve the plain rule as the main reference because its transfer is simpler and more consistent; do not promote the spectacular but non-transferring LT30 hybrid.

A durable edge remains unproven. The next defensible evaluation is frozen observation against equal-weight holding, measuring actual fills, matched gross exposure, intraday risk and relative performance. The research does not justify live activation.

No production bot, saved stable design, observer service or paid-data subscription was changed. Protocols, failed branches, data, code, outputs and dependency hashes are retained. See RUN.txt.
"""
(P/'REPORT.txt').write_text(text)
print('Report saved')
