from pathlib import Path
import pandas as pd,json,numpy as np
P=Path(__file__).resolve().parent;q=pd.read_csv(P/'scores.csv');y=pd.read_csv(P/'yearly.csv');u=pd.read_csv(P/'uncertainty_controls.csv');base=q[(q['mode']=='recovered')&(q.cost=='three_cents')&(q['sample']=='all_dates')];years=y[(y['mode']=='recovered')&(y.cost=='three_cents')&(y['sample']=='all_dates')];costs=q[(q['mode']=='recovered')&(q['sample']=='all_dates')].pivot(index='period',columns='cost',values='net');models=q[(q['mode']=='recovered')&(q.cost=='three_cents')&(q['sample']=='model_dates')];rnd=u[u['sample']=='all_dates'];rec=json.loads((P/'reconciliation.json').read_text())
def table(x):return x.to_markdown(index=False,floatfmt=',.2f')
text='''# Standalone opening momentum: completed backtest

**Conclusion: no robust, execution-validated edge. The previous $11,895 diagnostic is reproduced exactly, but does not validate trading the simple rule every day.** At the three-cent cost scenario the standalone rule loses $42,090 over the full available history. A cheaper one-cent scenario is positive, so cost feasibility matters; neither scenario is a verified fill record.

## Rule and data

Each month use the 30 most liquid stocks selected from the previous month's median daily dollar volume, with the existing universe's $5 price and coverage criteria. Use historical constituents rather than today's winners. Rank exact 09:30-open-to-09:59-close returns. Buy the strongest and short the weakest, approximately $50,000 per leg, integer shares. Select names before observing entry availability. Attempt both entries at 10:01 ET; if either bar is absent, record an unfilled pair and select no substitute. Exit at 10:30 or the first positive-volume bar through 10:35 when 10:30 is absent. No stops, targets, fitted ranking model, or new entry filter.

Available history June 1, 2018–July 29, 2026: 2,050 eligible dates, 2,048 filled pairs, two skipped pairs, zero unresolved exits after archived-price recovery. This is approximately $100,000 gross exposure, NOT a capital/margin requirement. Returns use trade-bar prices, not contemporaneous bid/ask execution. Drawdown is from closed daily P&L, not intratrade mark-to-market.

Base costs: three cents per share per side PLUS 0.5 basis points of traded dollar value per side. Short borrow is excluded in base and separately stressed. These assumptions are explicit scenarios, not account-specific IBKR/Webull charges. No live orders or paid data downloads.

## Standalone results

'''+table(base[['period','filled','net','mean','win','worst','dd','without_best5']])+'''

`mean` is net per filled pair; `win` is a fraction; `without_best5` removes the five best pair-days from that period. All includes partial 2018, whereas development starts 2019. The recent positive aggregate combines **2025 loss of $13,403** with **2026-to-July gain of $21,468**. It is not a consistently profitable recent record. Removing the five best recent days changes +$8,065 into −$5,124.

'''+table(years[['year','filled','net','mean']])+'''

## Costs and execution sensitivity

Dollar net P&L; one/three/five cents are per share PER SIDE, each with the 0.5-bps/side allowance. `five_bps` instead charges five basis points per side. `borrow3`/`borrow10` add annual 3%/10% borrow to base, charging a full calendar day on short entry notional. These rates are illustrative and can understate hard-to-borrow costs. `fill_extra` adds another one basis point per side to base.

'''+costs.to_markdown(floatfmt=',.2f')+'''

At the optimistic one-cent assumption all earlier/recent aggregate periods are positive, but development earns only $2,298 and relies on a large GME short; this is not evidence that the execution/borrow assumption was achievable. Full-history break-even, after the 0.5-bps allowance and before borrow, is about 1.93 cents/share/side. Recent break-even is 4.28 cents, illustrating that one universal cent-per-share assumption can conceal price/liquidity changes.

A separately replayed 10:02-entry/10:31-exit variant with identical opening signals cuts recent three-cent P&L from $8,065 to $3,340. Its development and validation results are negative. This changes both execution times; it is sensitivity to a delayed schedule, not an estimated broker latency distribution. Some delayed historical exits still have adverse missing-data marks; no favorable interpretation relies on them.

## Does the model's date selection help?

Using the simple ranking on the frozen model's active dates, with the full eligible opening universe and recovered exits:

'''+table(models[['period','filled','net','mean','dd','without_best5']])+'''

This selected-date hybrid gains $9,552 in total, but its $11,402 best pair-day exceeds that entire gain. Removing the five best days yields −$17,695. Closed-day drawdown is $30,810. Its earlier period loses $12,674. It is an adaptive diagnostic, not a newly validated hybrid.

The original $11,894.78 recent result used only the model's feature-complete stocks and first-available bars within its old timing windows. Replaying that exact universe and convention reproduces all 938 pair-days to floating-point precision. The new $12,684 recent selected-date figure uses the full signal-eligible universe and exact opening/entry clocks. The changed figure is explained by changed definitions, not claimed as extra discovered profit. Compatibility outputs for both universes remain in scores.csv.

## Random controls and uncertainty

2,000 random ordered long/short portfolios choose from the same day's eligible names, at the same leg notional and costs. The same entry availability and recovered-exit rules apply. Monthly-block resampling gives pointwise 95% intervals for mean pair-day P&L; they are not adjusted for accumulated research selection.

'''+table(rnd[['period','net','ci_low','ci_high','random_median','random_p025','random_p975','fraction_random_below']])+'''

The rule beats most random controls, including recently. That supports relative information in opening ranks, but random portfolios lose substantially to costs: beating them does not establish positive achievable expectancy. The recent interval spans roughly −$48 to +$91 per trade, and the full-history interval also crosses zero. No inference of durable profitability follows.

## Missing-price correction and limits

The initial exact-clock replay assigned two adverse 10% loss marks when exits were absent. Archived bars resolve both at 10:32: GME on February 1, 2021 and BKNG on November 7, 2022. Replacing the placeholders improves aggregate net by $20,473; it does not make the standalone base case profitable. The GME short alone gains about $10,655 before adding its paired long. Borrow/locate availability at that event is unverified, so even that favorable archival price is not proof of an executable short. The same first-available-exit recovery was applied to ALL names before rerunning random controls. Raw strict-clock outputs are retained under mode `exact`; corrected outputs are mode `recovered`.

No selected name was removed for a poor outcome or missing exit. No recent threshold was optimized. However, recent data had already been inspected in earlier research: there is no pristine holdout or forward result here. The existing quote-file catalogue is dominated by option-chain data (folder inventory saved); executable stock bid/ask prices and borrow history have not been established for this replay. This backtest therefore answers the historical rule question, not a live execution certification.

## Reproduction and verification

Run build.py, run.py, assess.py, repair_exits.py and report.py with the existing Python environment. Input prices are opening_leader_2026_07/data/minutes_YEAR.parquet with historical id maps and universe.parquet. Reconciliation also reads the earlier conditional model ledgers. Protocol and its explicit missing-exit correction are in PROTOCOL.md. Trades, leg ledgers, all cost/clock/universe outputs, annual tables, random controls, symbol contributions and reconciliation are saved in this folder. No production code was changed.
'''
(P/'REPORT.md').write_text(text)
# Accounting and claim checks, not implementation-mirroring test suite.
t=pd.read_parquet(P/'trades.parquet');l=pd.read_parquet(P/'legs.parquet');check=l.groupby(['mode','clock','day']).net.sum();b=t[t.cost=='three_cents'].set_index(['mode','clock','day']);assert np.allclose(b.loc[check.index,'net'],check);assert rec['n']==rec['old_n']==938 and rec['max_abs_difference']<1e-8;assert json.loads((P/'audit.json').read_text())['missing_exit']==0
(P/'verification.json').write_text(json.dumps({'leg_pair_accounting':'pass','old_diagnostic_reproduction':'pass','recovered_selected_missing_exits':0,'report_generated_from_scores':True},indent=2));print('report and checks complete')
