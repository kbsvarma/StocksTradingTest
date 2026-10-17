from engine import P,CONFIG
import pandas as pd
import json

r=pd.read_csv(P/'results.csv');s=pd.read_csv(P/'sensitivity.csv');f=pd.read_csv(P/'frozen_selection.csv');a=pd.read_csv(P/'additional_checks.csv');bs=pd.read_csv(P/'bootstrap_diagnostics.csv');roll=pd.read_csv(P/'rolling_summary.csv');walk=pd.read_csv(P/'walkforward.csv').iloc[0]
labels={'CHPY_hold':'Hold CHPY; reinvest payouts','CHPY_cash_dividends':'Hold CHPY; retain payouts as cash','capture_b1_a0_open':'Buy prior close; sell ex-date open','capture_b1_a0_close':'Buy prior close; sell ex-date close','capture_b1_a1_close':'Buy prior close; sell next-day close','capture_b3_a0_close_chpy20':'3-day capture with 20-day trend filter','capture_b5_a5_close':'Buy 5 sessions before; sell 5 after','weekday_0_3':'Monday open to Thursday close','rebound_close_a3':'Buy ex-date close; sell 3 sessions later','recover_price_s0_h20':'Wait for share-price recovery; 20-session limit','recover_total_t0.005_s0.03_h5':'Target 0.5% net; 3% close-triggered stop; 5-session limit','capture_b3_a5_close':'Hindsight winner: buy 3 before; sell 5 after'}
focus=list(labels)
te=r[(r.period=='test2026')&r.name.isin(focus)].copy();te['approach']=te.name.map(labels);te.to_csv(P/'strategy_comparison.csv',index=False)

def tab(frame,cols):
    out=['| '+' | '.join(label for key,label,fmt in cols)+' |','|'+'|'.join('---' for c in cols)+'|']
    for row in frame.to_dict('records'):
        out.append('| '+' | '.join(fmt(row[key]) for key,label,fmt in cols)+' |')
    return '\n'.join(out)
money=lambda x:f'${x:,.2f}'
pct=lambda x:f'{x:.2%}'
text=str
tablecols=[('approach','Approach',text),('ending','Ending account',money),('profit','Net profit',money),('max_drawdown','Max drawdown',pct),('closed_trades','Closed trades',lambda x:str(int(x)))]
metrics=te.set_index('name')
overnight=metrics.loc['capture_b1_a0_open'];trend=metrics.loc['capture_b3_a0_close_chpy20']
recovery=metrics.loc['recover_price_s0_h20']
half=s[(s.name=='capture_b3_a0_close_chpy20')&(s.period=='test2026')&(s.allocation==.5)].iloc[0]
f['approach']=f.selected.map(labels).fillna(f.selected)
fam=r[(r.period=='test2026')&(r.family!='benchmark')].groupby('family').agg(rules=('name','size'),profitable=('profit',lambda x:int((x>0).sum())),median_profit=('profit','median'),worst_profit=('profit','min'),best_profit=('profit','max')).reset_index()
cost=pd.concat([te[te.name.isin(['capture_b1_a0_open','capture_b3_a0_close_chpy20'])],s[(s.period=='test2026')&s.name.isin(['capture_b1_a0_open','capture_b3_a0_close_chpy20'])&(s.allocation==1)&s.whole_shares]]).sort_values(['name','cost_bps']);cost['approach']=cost.name.map(labels)
recent=r[(r.period=='recent3m')&r.name.isin(['CHPY_hold','capture_b1_a0_open','capture_b3_a0_close_chpy20','recover_price_s0_h20','capture_b3_a5_close'])].copy();recent['approach']=recent.name.map(labels)
full=r[(r.period=='full')&r.name.isin(['CHPY_hold','capture_b1_a0_open','capture_b3_a0_close_chpy20','recover_price_s0_h20'])].copy();full['approach']=full.name.map(labels)
weekly=te[te.name.isin(['CHPY_hold','capture_b1_a0_open','capture_b3_a0_close_chpy20','recover_price_s0_h20'])]
rolling=roll[roll.name.isin(weekly.name)].copy();rolling['approach']=rolling.name.map(labels)
decomp=te[te.name.isin(['capture_b1_a0_open','capture_b3_a0_close_chpy20','recover_price_s0_h20'])]
monthly=[]
for name in weekly.name:
    d=pd.read_csv(P/'daily'/f'{name}_test2026.csv',parse_dates=['Date']).set_index('Date')
    v=d.equity.groupby(d.index.to_period('M')).last();prev=[10000]+v.tolist()[:-1]
    for month,value,before in zip(v.index,v,prev):monthly.append(dict(name=name,month=str(month),profit=value-before,total_return=value/before-1,ending=value))
pd.DataFrame(monthly).to_csv(P/'monthly_results.csv',index=False)

report=f'''# CHPY: $10,000 weekly trading and dividend-capture deep dive

**Several approaches made money historically. The strongest practical candidate from this search was a short capture trade gated by an established uptrend. Pure overnight capture had modest, cost-sensitive profit; waiting indefinitely for the share price to recover exposed the account to large losses. None established dependable weekly income or a statistically robust dividend-specific edge.**

## What was tested

119 trading rules plus four holding references, using 369 actual daily price sessions and 76 audited distributions. The main chronological evaluation is **January 2–September 25, 2026**, starting with **$10,000**. Earlier observations through December 31, 2025 select rules separately from their later evaluation. The complete price sample is April 8, 2025–September 25, 2026.

This is retrospective research. CHPY and the idea of these rule families were selected after seeing some later returns in previous research. A chronological split prevents directly choosing parameters on 2026 results, but is not pristine prospective validation.

Baseline: whole shares; one position; no margin or capital additions; 0.10% trading cost per side (approximately $10 for each $10,000 purchase or sale); actual CHPY payment dates; T+1 sale settlement; zero idle-cash interest; before tax. Each table row is a separate $10,000 simulation, not simultaneous use of the same account across all strategies. Profits generally compound. Fixed-$10,000 maximum trade size and half-account deployment are examined separately.

{tab(fam,[('family','Family',text),('rules','Rules',text),('profitable','Profitable in 2026',text),('median_profit','Median net profit',money),('worst_profit','Worst net profit',money),('best_profit','Best net profit',money)])}

These high profitable counts occurred in a very strong sector market. They are not independent evidence of 110 distinct edges. Multiple rules have overlapping holdings, and the favorable sample can make many weak timing rules profitable.

## Main $10,000 results

{tab(te,tablecols)}

All profits include share-price changes, accrued distributions and transaction costs. Open holdings are marked at the cutoff. Maximum drawdown is the decline from the account's running peak, not necessarily a loss from the original $10,000. The dollar drawdown can exceed the percentage applied to initial capital after the account has grown.

![Account value and drawdowns](equity_and_drawdown.png)

## The candidate that merits further testing

The **3-day capture with 20-day trend filter** was the filtered-family winner using only 2025 training results, under the prewritten constraints of at least 12 closed trades and no more than 15% training drawdown.

Its exact rule is:
1. Use the published ex-date calendar. Schedule a purchase at the close three trading sessions before the ex-date.
2. Before entering, require the **previous session's CHPY total-return index** to exceed its own trailing 20-session average. The index includes past distributions, so a mechanical ex-dividend price drop is not mistaken for a trend change.
3. Buy whole shares with available settled strategy cash. Skip if less than 95% of the intended allocation is available; never add outside funds.
4. Sell at the close on the ex-date. The position has earned that distribution; it need not be held through the payment date.
5. Skip overlapping signals. Leave cash idle when the filter fails. No discretionary overrides or retrospective choice of a better weekday.

2025 training profit was **$2,479.64**, maximum drawdown **5.42%**, across 28 closed trades. Its 2026 evaluation produced **$4,046.36** net profit, **11.29%** maximum drawdown, 25 closed trades and a 68% trade win rate. Its worst closed trade lost **$753.16**. It had 19 profitable weeks, 11 losing weeks and nine flat weeks. Mean weekly profit was **$103.75**, while median weekly profit was **$0**; the average is not a dependable weekly payment.

Deploying roughly **50% of current account equity per trade** produced **${half.profit:,.2f}** profit and **{abs(half.max_drawdown):.2%}** maximum account drawdown. Worst week lost **${abs(half.worst_week_dollars):,.2f}**. The idle portion earned zero. This reduces observed exposure; it does not impose a guaranteed maximum loss.

Keeping a **$10,000 maximum purchase budget** instead of compounding the whole account produced **$3,561.57** profit and **8.30%** drawdown. Gains remained in cash; losses could reduce the next trade size, with no top-ups.

There were weaknesses: July lost **6.60%** and August **4.33%** in the continuous 2026 run. A fresh June 25 start earned only **$190.93** by September 25 and still experienced a **10.72%** drawdown. It is a candidate for prospective paper tracking, not a proven high-return income allocation.

## Does dividend scraping work?

For the closest version to simply taking the payout—buy immediately before ex-date, sell at the next open—the 2026 accounting is:

- Distributions accrued/received: **$3,428.25**.
- Aggregate share-price P&L: **−$1,277.41**.
- Trading costs: **−$832.44**.
- Net account profit: **$1,318.41**.

Thus the payout amount significantly overstates the profit. Across 38 completed overnight trades, 23 won and 15 lost; average net profit was **$34.69 per trade**, worst trade **−$393.97**, best **+$441.17**. Across all 39 calendar-week buckets, mean profit was $33.81. This strategy retained distribution entitlement after selling at ex-date open; buying on the ex-date itself would not earn that payout.

Execution matters. Waiting until ex-date close, instead of selling at the open, reduced 2026 net profit to **$474.62** and increased drawdown to **17.05%**. This is evidence that the actual holding interval and market moves matter, rather than the mere receipt of cash.

{tab(cost,[('approach','Approach',text),('cost_bps','Cost per side, bps',lambda x:f'{x:.0f}'),('profit','2026 net profit',money),('max_drawdown','Drawdown',pct)])}

At **0.30% per side**, overnight capture lost **$269.23**. The filtered rule remained profitable at **$2,716.66**. The current issuer median spread is 0.15% full bid/ask width; that does not guarantee executions at our 0.10% one-way baseline, especially at the open. Historical order-book depth is unavailable.

![Costs and irregular weekly profit](costs_and_weekly_profit.png)

## Why “wait for recovery and keep the dividend” is dangerous

The price-recovery rule buys before ex-date, waits until net sale proceeds alone cover the purchase including costs, and then sells next open. It keeps distribution cash as additional profit. The no-stop version still has a 20-session time limit so capital cannot remain trapped without accounting for its market value.

It won **78.26%** of closed 2026 trades, but earned only **$1,116.49** with **28.15%** drawdown. One trade opened June 30 and exited July 29 at the time limit:

- Share-price loss: **$3,574.17**.
- Distributions collected: **$449.51**.
- Costs: **$23.04**.
- Net loss: **$3,147.70**.

Many small wins did not prevent one large loss. A 0.5% total-profit target with a 3% close-triggered stop and five-session cap produced only **$284.27** in 2026 with **24.27%** drawdown. Close-triggered stops execute next open and can lose more than the threshold because of gaps. They are not guaranteed intraday stop fills. Recovery strategies can also change their exact exit dates when costs or share rounding change, so their cost-sensitivity results need not be monotonic.

## Was the edge really about the dividend?

The same overnight entry/exit signals applied to **SMH** produced **$1,869.76** profit, versus **$1,318.41** for CHPY; **SOXX** produced **$2,123.05**. Those funds did not need CHPY's weekly payout to make money on those dates.

The trend-filtered CHPY signal applied to SMH made **$3,472.57**, while applying it to SOXX made **$4,979.96**. CHPY's **$4,046.36** sat between them, with a smaller observed drawdown than either same-signal sector reference. These are imperfect comparisons because holdings, weighting, volatility and option exposure differ. They show that broad semiconductor timing is a plausible explanation for a large part of the profit; they do not isolate a causal dividend advantage.

Four-week block bootstrap intervals for average weekly returns crossed zero for both the overnight and filtered candidates. A separate comparison with contemporaneous sector exposure produced no significant positive excess-return result after false-discovery adjustment across 119 rules. These short-sample diagnostics do not prove an edge cannot exist. They mean this research has not established one robustly.

## Frozen choices, monthly adaptation, and recent weakness

Each family chose its most profitable eligible 2025 rule and carried that exact rule into 2026:

{tab(f,[('family','Family',text),('approach','Frozen selection',text),('train_profit','2025 training profit',money),('test_profit','2026 test profit',money),('test_drawdown','2026 drawdown',pct)])}

The training drawdown cap was a selection criterion, not a future guarantee. Most selected rules subsequently exceeded it.

A monthly adaptive strategy selected from all trading rules using the preceding 126 sessions, at least eight closed trades and at most 15% training drawdown. Existing trades retained their original exits. That persistent account earned **${walk.profit:,.2f}**, with **{abs(walk.max_drawdown):.2%}** drawdown and a worst week of **−${abs(walk.worst_week_dollars):,.2f}**. It did not provide dependable weekly income. Its complete monthly choices are saved in `monthly_selection.csv`.

The hindsight 2026 winner made **$8,374.78**, but lost **$696.50** from a fresh June 25 start. It was not the 2025-selected capture rule and must not be presented as a rule we knew to choose beforehand.

Fresh June 25–September 25 accounts:

{tab(recent,tablecols)}

## Consistency and longer available history

{tab(weekly,[('approach','Approach',text),('winning_weeks','Up weeks',text),('losing_weeks','Down weeks',text),('flat_weeks','Flat weeks',text),('mean_weekly_profit','Mean weekly P&L',money),('median_weekly_profit','Median weekly P&L',money),('worst_week_dollars','Worst week',money)])}

For 64 overlapping 13-calendar-week windows starting at each available week's first session:

{tab(rolling,[('approach','Approach',text),('profitable_fraction','Profitable windows',pct),('median_profit','Median profit',money),('worst_profit','Worst profit',money),('best_profit','Best profit',money)])}

These windows overlap heavily and are not 64 independent experiments. All end within the available data.

Full available April 8, 2025–September 25, 2026 results:

{tab(full,tablecols)}

The vendor misses the first three trading sessions after CHPY's launch. The available start immediately preceded a 17.46% daily rebound; full-history holding results are particularly sensitive to that entry. This is why the main table emphasizes 2026 and includes later restarts. Less than 18 months cannot represent a prolonged semiconductor bear market.

## How the simulation avoids overstating the result

- Dividend-adjusted prices are not used for cash accounting. Actual close/open prices and audited cash distributions are counted separately, once.
- The account accrues a distribution only on shares owned before ex-date open. It cannot receive a distribution merely by buying on the ex-date. Cash is unavailable until the actual payment date; dividend entitlement remains after selling.
- Sale proceeds are unavailable until the next trading session. All account cash is tracked, including receivables and open-position value. No margin, duplicated capital or fresh deposits are used.
- Whole-share quantities are used. The 95%-funding requirement prevents tiny residual cash balances from creating misleading full-sized “trades.” Earlier $100,000 research allowed small partial entries and used 5 bps each side, so multiweek results should not be obtained by simply dividing those earlier results by ten.
- Scheduled open/close executions are approximations, with explicit cost stresses. Daily bars cannot establish available quotes, intraday scalping fills, limit-order execution or maximum intraday drawdown.
- Trend filters use only previous-session data. Recovery targets/stops use completed closes and execute at the following open. No same-close reaction to newly observed prices.
- Overlapping signals are skipped, not stacked. Entry times differ when a long trade or unsettled proceeds consume an otherwise eligible signal; longer weekly windows can collect more than one distribution.
- Calendar-week rules skip a holiday entry weekday. Scheduled exits on holidays move to the next actual trading session. Reported calendar-week P&L includes the first partial week and September's partial final month.
- The dividend calendar is assumed available in advance; historical publication timestamps were not reconstructed. A rule requiring a future date before its announcement could be infeasible. No future payout amounts are used for entry selection.
- References SMH/SOXX use vendor payouts and a modeled five-calendar-day payment lag, rather than separately audited issuer pay dates. They have no splits in the tested sample. Their portfolios are not identical to CHPY.
- Cash-flow identities reconcile each base path to within $0.000001. Independent synthetic tests verify that a $1 ex-date price drop exactly offset by a $1 payout creates no profit before costs, and a loss after costs. Actual overnight trades were independently reconstructed from raw prices and issuer amounts.
- Taxes are not deducted. CHPY distributions can have differing tax character, including return of capital; this research does not incorrectly apply one uniform dividend tax rate. Frequent trading can change tax outcomes, and after-tax results require account and tax-lot details.

Sources: [CHPY issuer and payout history](https://yieldmaxetfs.com/our-etfs/chpy/), [SEC investor explanation of ex-dates](https://www.investor.gov/introduction-investing/investing-basics/glossary/ex-dividend-dates-when-are-you-entitled-stock-and), [FINRA T+1 settlement](https://www.finra.org/investors/insights/understanding-settlement-cycles), [IRS distribution tax treatment](https://www.irs.gov/taxtopics/tc404). Price data are saved in the preceding CHPY research folder; source hashes are in `audit.json`.

## Decision

There is enough historical profit to justify further prospective testing of the filtered three-day rule, particularly with partial account exposure. Pure dividend scraping is a thin, execution-sensitive result, and the same overnight sector trades weaken the claim that the payout itself is the source of profit. Waiting for price recovery has the capital-loss pattern the user wants to avoid. No rule here supports promising a fixed weekly payment or deploying the $10,000 on the strength of this backtest alone.

No brokerage orders, live bot or scheduled automation were created. Reproduction: `python3 run.py`, `python3 validate.py`, `python3 write_report.py`; plots use `plot.py` with matplotlib. All 123 configurations, 492 base paths, trade ledgers, daily account curves, monthly outcomes, rolling windows and cost sensitivities are saved beside this report.
'''
(P/'REPORT.md').write_text(report)
print('Wrote',P/'REPORT.md','words',len(report.split()))
