from pathlib import Path
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
O=pd.read_csv(P/'outcomes.csv');S=pd.read_csv(P/'summary.csv');D=pd.read_parquet(P/'daily.parquet');A=pd.read_csv(P/'annual.csv');details=json.loads((P/'details.json').read_text())
def table(df):return df.to_markdown(index=False)
fig,axes=plt.subplots(3,2,figsize=(13,12));colors={'daily_50_50':'#087f8c','hold_50_50':'#7b7b7b','universal':'#aa6a12'}
for ax,(_,r) in zip(axes.flat,O.iterrows()):
 for strategy,label in [('daily_50_50','Daily 50/50'),('hold_50_50','Hold initial 50/50'),('universal','Cover universal')]:
  d=D[(D.pair==r.pair)&(D.cost_bps==5)&(D.strategy==strategy)]
  ax.plot(d.date,d.wealth/1000,label=label,color=colors[strategy],lw=1.5)
 ax.set_title(r.pair.replace('_',' / ')+(' — screen passed' if r.promising else ' — screen failed'));ax.set_ylabel('$ thousands, starting $100k');ax.grid(alpha=.2);ax.legend(fontsize=8)
axes.flat[-1].axis('off');axes.flat[-1].text(0,1,'Frozen selection: 2019–2021\nTest: Jan 2022–Sep 11, 2026\n\n5 bps per dollar bought/sold\nDividends and splits adjusted\nFractional shares, no leverage\nSeparate $100k experiment per pair\n\nHistorical screening; not verified fills.\nFailures retained; no pair substitutions.',va='top',fontsize=12)
fig.suptitle('Five non-AI stock pairs: daily constant-weight rebalancing',fontsize=16);fig.tight_layout(rect=[0,0,1,.97]);fig.savefig(P/'equity_curves.png',dpi=150);plt.close(fig)
rows=[]
for _,r in O.iterrows():rows.append({'Pair':r.pair.replace('_',' / '),'Daily CAGR':f'{r.cagr:.2%}','Hold CAGR':f'{r.hold_cagr:.2%}','CAGR advantage':f'{r.advantage_pp:+.2f} pp','Daily final $':f'{r.wealth:,.0f}','Worst drawdown':f'{r.max_drawdown:.2%}','Screen':'PASS' if r.promising else 'FAIL'})
text='''# Five non-AI pairs: first-stage results

Two of five frozen candidates passed the predeclared historical screen. GILD/UAL is the stronger lead; BIIB/EXPE is a weaker secondary lead. These are candidates for independent data verification, not established live edges.

## What was tested

The existing local Yahoo price cache supplied daily adjusted closes, dividends and splits. Entry valuation was December 31, 2021; test returns run January 3, 2022–September 11, 2026 (1,177 sessions, 4.695 years). Each pair starts with its own $100,000. The primary design restores 50/50 every close, with fractional shares, no leverage, forecasts, switches or optimized weights. All comparisons below charge **5 basis points per dollar bought or sold**, including entry and liquidation. Taxes are excluded. This models ideal close-to-close total-return allocations, not a verified auction execution system.

Selection used 2019–2021 only: an archived 150-stock liquid universe, explicit business exclusions, then volatility, modest prior growth and weak pair correlation. There were 31 eligible stocks and 132 eligible pairs. A matching optimization selected five disjoint pairs maximizing prior relative-return variance, not subsequent portfolio returns. The frozen protocol, entire exclusion audit, ranking and source hashes are saved alongside this report. User holdings, Mag 7 and AI infrastructure were excluded.

The business list is a present-day interpretation of the user's mandate, not historical classification. Expedia is treated as a travel business despite using technology and AI. Established diversified drug manufacturers were allowed; this does not mean their stocks lack drug-development, pandemic or product hype. The selection window includes COVID disruption and can overweight pharmaceutical/travel relationships. Cached history has been examined in previous research: the chronological split is not a pristine untouched experiment. Coverage and surviving cached names also prevent a survivorship-free claim.

## Primary results

'''+table(pd.DataFrame(rows))+'''

The screen required positive CAGR, at least +2 percentage points versus holding the same pair, positive relative log growth in both 2022–2023 and 2024–latest, continued advantage at 10 bps, complete data, and a drawdown no worse than -60% or more than 5 points worse than holding. These thresholds were frozen before evaluation; they are research conventions, not Cover's theorem.

- **GILD/UAL:** $100k grew to $279,112 versus $241,594 for holding the pair. It also beat either stock individually (GILD 19.69%, UAL 21.61% CAGR). This is the clearest candidate. Both stocks rose substantially; most portfolio growth came from their own appreciation, not rebalancing alone.
- **BIIB/EXPE:** $137,617 versus $123,905. Lower drawdown than either constituent, but lower CAGR than Expedia alone (10.14%). Its estimated relative-return uncertainty interval includes zero. Secondary lead only.
- **AAL/DLTR:** rebalancing reduced the loss but still finished below starting capital. This is particularly instructive: volatility harvesting does not rescue every poor pair.
- **AMGN/BA:** profitable, but the +1.12-point advantage failed the predeclared minimum. Amgen alone returned 15.18% CAGR.
- **MPC/PFE:** rebalancing repeatedly sold the much stronger Marathon position. It earned money but severely lagged holding. No candidate replacement was made after observing this failure.

## Stability and uncertainty

The following are cumulative relative log returns versus holding; positive means the rebalanced portfolio won over that subperiod. Bootstrap intervals are 95% intervals for annualized mean daily relative **log return**, using 2,000 moving-block samples of 20 sessions. They are conditional on selection and reused history, are not CAGR intervals, and are not corrected for research selection or multiple testing.

'''
b=[]
for _,r in O.iterrows():b.append({'Pair':r.pair,'2022–23 relative log':f'{r.relative_log_2022_2023:.3f}','2024–latest relative log':f'{r.relative_log_2024_on:.3f}','Annual log interval':f'[{r.bootstrap_annual_log_lower:.2%}, {r.bootstrap_annual_log_upper:.2%}]'})
text+=table(pd.DataFrame(b))+'\n\n## Costs, comparators and yearly returns\n\n'
c=S[S.strategy=='daily_50_50'].pivot(index='pair',columns='cost_bps',values='cagr');c=c.map(lambda x:f'{x:.2%}').reset_index();c.columns=['Pair','0 bps CAGR','5 bps CAGR','10 bps CAGR'];text+=table(c)+'\n\n'
c=S[(S.cost_bps==5)&S.strategy.isin(['daily_50_50','hold_50_50','universal','hindsight_oracle'])].pivot(index='pair',columns='strategy',values='cagr');text+=table(c.map(lambda x:f'{x:.2%}').reset_index())+'\n\nThe oracle chooses its constant weight using the entire test and is not an executable selection method. Universal weights depend only on preceding returns; a uniform prior over weights is updated daily. These comparisons do not alter the primary fixed-weight design.\n\n'
c=A[(A.cost_bps==5)&(A.strategy=='daily_50_50')].pivot(index='pair',columns='year',values='return_net');text+=table(c.map(lambda x:f'{x:.2%}').reset_index())+'\n\n2026 is a partial year through September 11, not annualized.\n\n## Why this is compatible with the theory, without proving the story repeats\n\nFor each gross daily return vector, the identity is `log(mean(gross returns)) = mean(log(gross returns)) + diversification bonus`. The bonus is nonnegative before costs. It is measured against the weighted geometric growth of constituents, not automatically against a drifting buy-and-hold portfolio or the better stock.\n\n'
b=[]
for x in details:b.append({'Pair':x['pair'],'Stock log growth/year':f"{x['weighted_stock_log_growth']:.2%}",'Diversification log bonus/year':f"{x['diversification_log_growth']:.2%}",'Training correlation':f"{x['training_rho']:.3f}",'Test correlation':f"{x['test_rho']:.3f}"})
text+=table(pd.DataFrame(b))+'''

The cited Cover historical result, 73.619x over 22 years, corresponds to roughly 21.58% CAGR. GILD/UAL's 24.43% over 4.70 years is in that annualized numerical range; it is not a replication of the 22-year result or evidence it can persist for 22 years. The other pairs illustrate why that example is not an expected return target.

## Separation from existing investments

No selected ticker overlaps the named holdings. Return correlation with an equal-weight daily-return proxy of META, GOOGL, MSFT, NVDA, AMD, AVGO, AMZN, MU, DELL, INTC and TSM is 0.453 for GILD/UAL and 0.408 for BIIB/EXPE. Their mutual correlation is 0.521. They offer different businesses, but not independent market risk. The proxy is not the user's actual portfolio or actual weights. Four selected pairs include drug manufacturers and three include airlines/aerospace; disjoint tickers do not eliminate sector concentration.

## Verification completed and remaining

All selected price paths were complete. Thirty independent share-ledger reconciliations (two primary strategies × three costs × five pairs) agreed with the return engine to numerical precision. Gross buy-and-hold, fixed-weight growth decomposition and universal-mixture identities passed. Increasing the universal grid from 1,001 to 10,001 weights changed terminal wealth by less than 0.00004% for every pair. No adjusted-return versus close-plus-dividend discrepancy exceeded 0.5 percentage points.

Five daily moves exceeded 20%; all are retained in `price_audit.csv`. Biogen's September 28, 2022 move follows its [September 27 positive lecanemab trial release](https://investors.biogen.com/news-releases/news-release-details/lecanemab-confirmatory-phase-3-clarity-ad-study-met-primary). Dollar Tree's flagged dates coincide with its [May 26, 2022 earnings](https://corporate.dollartree.com/news-media/press-releases/detail/224/dollar-tree-inc-reports-results-for-the-first-quarter) and [September 4, 2024 earnings](https://corporate.dollartree.com/investors/news-events/press-releases/detail/271/dollar-tree-inc-reports-results-for-the-second-quarter). These releases establish event context, not independent verification of exact price moves. The AAL/UAL April 9, 2025 jumps and all vendor prices remain to be independently reconciled.

Daily adjusted prices approximate reinvested cash distributions. Exact fixed weights at the unknown closing print are idealized: execution testing must specify a causally submit-able order, auction deadlines, residual weight error and real transaction costs. The chosen 5/10-bps scenarios do not establish actual fills. Dividend timing, stock identity, corporate actions, taxes, historical universe coverage and vendor differences remain material.

## Next step

Verify **GILD/UAL first**, then **BIIB/EXPE**, using the unchanged frozen rules. The separate `DATABENTO_VERIFICATION.md` specifies the requested data and checks. No paid data was acquired. Another data vendor verifies measurements; it does not create a new out-of-sample period. After reconciliation, use a frozen forward paper test to evaluate execution and persistence before tuning weights or rebalance frequency. Do not search replacements until five historical winners appear.

## Files and reproducibility

- `PROTOCOL.md`, `selection.json`, `selected_pairs.csv`: frozen rules and selections.
- `universe_audit.csv`, `all_qualifying_training_pairs.csv`: all exclusions and eligible scores.
- `summary.csv`, `annual.csv`, `outcomes.csv`, `details.json`: results and decisions.
- `daily.parquet`, pair price CSVs: complete curves and evaluation inputs.
- `verification.csv`, `price_audit.csv`, `correlations.csv`: accounting, data and exposure diagnostics.
- `run_pairs.py` imports the existing accounting engine whose hash is in `run_metadata.json`; run using `/opt/anaconda3/bin/python` from this repository.
- `equity_curves.png`: all five experiments, including failures.
'''
(P/'REPORT.md').write_text(text)
