from pathlib import Path
import pandas as pd,json
P=Path(__file__).resolve().parent
s=pd.read_csv(P/'summary.csv');a=pd.read_csv(P/'annual.csv');cov=pd.read_csv(P/'coverage_annual.csv');diag=pd.read_csv(P/'diagnostics.csv');unc=pd.read_csv(P/'uncertainty.csv');d=pd.read_csv(P/'daily.csv');sel=pd.read_csv(P/'selection.csv');tr=pd.read_parquet(P/'trades.parquet')
def table(frame):return frame.to_markdown(index=False,floatfmt='.2f')
def result_table(period):
 z=s[s.period==period].pivot(index='strategy',columns='cost_bps',values='cagr').reset_index();z.columns=['Strategy','Gross CAGR %','CAGR at 3 bps %','CAGR at 10 bps %','CAGR at 20 bps %'];return table(z)
net=s[(s.cost_bps==3)&(s.period=='All')][['strategy','dd','vol','win','avg_names','round_trips','missing_exits']]
net.columns=['Strategy','Max DD %','Annual volatility %','Winning nights %','Mean stocks/night','Stock round trips','Missing exits']
annual=a[(a.cost_bps==3)&(a.strategy.isin(['OBG','TopDecile','Universe']))].pivot(index='year',columns='strategy',values='total').reset_index()
m=json.loads((P/'download_manifest.json').read_text());ok=sum(x['status']=='ok' for x in m)
text=f'''# Overnight stock selection: completed free-data pilot

Research date: September 12, 2026. Entry sessions June 1, 2018–July 31, 2026; final exit August 3, 2026. 2,052 overnight portfolio observations. No leverage, no paid Databento data, no bot changes.

## Finding and decision

**There is a candidate direction here, but not a validated deployable edge.** The simple top-decile portfolio of prior overnight returns beats the same eligible universe in this sample, including recent data. The paper-inspired overnight-bias regression is weaker recently. Trading costs, substantial overnight drawdowns, missing historical companies and auction execution prevent a promotion claim.

Keep both candidate definitions frozen. If investigating this direction further, prioritize validating the top-decile portfolio's opening/closing execution and historical coverage. Do not splice it into Ridge or interpret these percentages as comparable to Ridge's fixed-contract dollar results. This study uses a fully funded, daily rebalanced equity portfolio with compounding, different exposure and different risk.

## What was tested

Before examining results, PROTOCOL.md fixed monthly selection rules and cost scenarios. The reference was [Lachance (2023), Night trading: Lower risk but higher returns?](https://onlinelibrary.wiley.com/doi/10.1002/rfe.1180). We adapted the idea of selecting stocks by their lagged overnight performance; this is not a replication of the paper's CRSP universe or market-cap-weighted implementation.

- **OBG:** Using the preceding 252 market sessions, regress each stock's log overnight return on its own log close-to-close total return. Select a positive intercept significant at one-sided 5%, with at least 240 valid observations.
- **TopDecile:** Select the strongest 10% by mean log overnight return over the same trailing window, rounding the count up. This was declared before the results, not added after OBG weakened.
- **Universe:** Buy every same-eligible common stock overnight without ranking.
- **SPYovernight:** Buy SPY at close, sell at next open.
- **SPYbuyhold:** Close-to-close total-return benchmark; no daily trading charge. Its exposure differs from the overnight strategies.

All equity baskets are equal weight, refreshed monthly using information through the prior month's close. Monthly membership is keyed to the entry date. A month-end purchase cannot use next month's ranking. Each selected basket is bought every regular-session close and sold at the next session's open, including weekend gaps. Cash earns zero. There is no fitted threshold search, regime rescue or leverage optimization.

## Returns and cost sensitivity

Costs are **round-trip basis points of traded notional per night**, not per side and not verified broker quotes. Three bps means $30 per $100,000 fully traded, before any costs omitted from that scenario. Fractional weights and scalable notional are assumed; integer shares, minimum ticket commissions, taxes and market impact are not modeled.

### Full sample: annualized compounded returns

{result_table('All')}

### January 2022 onward: annualized compounded returns

{result_table('2022+')}

### January 2025 onward: annualized compounded returns

{result_table('2025+')}

The high recent TopDecile return is observed historical performance, not a forecast. The 2022+ window is more sobering: at 10 bps its compounded return is negative. Costs apply every night, roughly 252 times per year, so even apparently small daily frictions accumulate substantially.

### Full-sample risk and trading frequency at 3 bps

{table(net)}

There are about five portfolio round trips each normal week. TopDecile normally holds around 13–14 stocks, implying roughly 65–70 individual stock round trips, or 130–140 buy/sell orders per week. It is not one small, low-risk trade. Drawdowns are marked at daily opening liquidation; the position can suffer larger losses before that mark. Returns from ex-dividend holdings include the dividend receivable, rather than assuming the ex-dividend opening gap is a pure loss; actual payment and cash availability occur later.

### Calendar-year returns at 3 bps (%)

{table(annual)}

2018 starts in June; 2026 ends in July. These rows are period returns, not annualized estimates.

## Selection benefit and fragility

The following estimates measure gross mean return improvement over the same eligible Universe portfolio. Intervals use 5,000 circular 20-session block bootstrap samples. They measure sampling variation conditional on this dataset, not survivorship bias, vendor errors, selection over previous research or future regime changes. The 2025+ block has already been examined and is not a pristine holdout.

{table(unc)}

OBG's recent intervals include zero. TopDecile's are positive in this conditional calculation, which supports further investigation rather than establishing tradable alpha. The baskets have different risk characteristics; excess mean return over Universe is not a risk-adjusted alpha estimate. Missing-exit stress marks also affect the full-sample comparison asymmetrically.

{table(diag)}

The break-even cost above solves for zero compounded return in that period. It is not a safe execution budget. The removal-of-best-five-nights calculation sets those portfolio returns to zero as a concentration diagnostic, not as an executable alternative strategy. Recent TopDecile results depend materially on large winning nights, although that diagnostic remains positive at 3 bps. Large losing gaps also occur; exits or stops cannot be assumed to execute before an overnight gap.

## Intraday execution cross-check

A separate archived five-minute cache covers May 20–July 2, 2026. We compared identical available stock-night pairs at official daily prices with a 9:35 exit and a 15:55 entry / 9:35 exit. This is a small, incomplete sensitivity sample, not a full alternate backtest or bid/ask execution simulator.

```
{(P/'execution_check.log').read_text().strip()}
```

Values are allocation-weighted means across covered pairs, not whole-portfolio daily means. In the TopDecile subset, delaying the exit reduces the measured mean substantially. This makes auction-quality data and actual execution observation central to further evaluation. A five-minute bar's open is itself a recorded trade, not a guaranteed fill. We did not choose either alternative time as a new strategy.

## Data limits and audits

The archived point-in-time monthly liquidity universe contains 529 different symbols, 150 names each month. Historical common-stock classification is taken from observations available before each month. Eligibility additionally requires enough training data and last-known as-traded price of at least $5. This avoids selecting only today's index constituents, but **does not remove survivorship bias from missing price histories**.

Retrieved {ok} of 530 requested symbol histories, including SPY. Missing histories remain in download_manifest.json and coverage.csv. FB→META is an explicit rename; BRK.B→BRK-B corrects the vendor's share-class punctuation. We did not replace acquired businesses with their acquirers. Comprehensive historical identifier continuity and delisting proceeds remain unverified.

{table(cov)}

These are stock-month counts after historical common-stock classification. “Short history” can represent an IPO or missing data; it is not automatically a vendor defect. Earlier coverage is materially worse than recent coverage. Missing histories can bias both selected and unselected portfolios, and the direction cannot be quantified from this cache.

Free daily Open/Close prices are split-adjusted, with cash dividends included explicitly. Future split factors only restore historical nominal prices for the $5 eligibility filter; they are not predictive inputs. AAPL's August 2020 split convention was spot-checked. Nine >50-bps discrepancies against the same vendor's adjusted-close total returns were flagged in corporate_action_audit.csv. None falls directly on a traded exit in this run, but historical signal estimates can still be affected by action data. This is a consistency check within one vendor, not independent price verification.

There are two missing selected exits for TopDecile (TSRO in January 2019), and four for Universe (ANDV, ESRX, TSRO). They are marked at **−100% of the affected allocation**, as specified in advance. These are deliberately harsh missing-data marks, not claims those businesses lost 100%. Later re-entry on sparse TSRO quotes is also a data-quality artifact, not an executable merger strategy. OBG has no missing selected exits, which does not cure its universe coverage bias. Missing intended entries remain cash; weights are not redistributed after seeing unavailable prices.

The full-sample TopDecile headline therefore includes unresolved artificial losses while still omitting other missing companies. Those effects cannot be assumed to cancel. The recent period has no missing selected exits but retains incomplete universe coverage.

## Completed validation and reproducibility

{(P/'VALIDATION.md').read_text().strip()}

Reproduce from this directory's cached prices with `python backtest.py`, then `python diagnostics.py`, `python execution_check.py`, and `python validate.py`. The downloader accesses free Yahoo history only. Cached data and code hashes are retained. selection.csv records every eligible monthly signal; trades.parquet records allocations and every stock-night return; daily.csv, summary.csv, annual.csv, uncertainty.csv and attribution.csv contain the results. No strategy was promoted and the current paper bot is unchanged.

## Practical next decision

**Worth pursuing: the frozen simple overnight-strength basket. Not justified: live deployment or claiming superiority to Ridge.** Before allocating paper capital to the reported return assumptions, establish historical official auction prices and actual feasible order costs for the frozen selections, resolve identifier/delisting gaps, and observe forward selection and fills without changing rules. The present evidence is enough to identify that specific next research target; it is not enough to promise a stable weekly dollar edge.
'''
(P/'REPORT.md').write_text(text)
print(P/'REPORT.md')
print(s[(s.strategy.isin(['OBG','TopDecile']))&(s.cost_bps.isin([3,10]))][['strategy','period','cost_bps','cagr','dd']].to_string(index=False))
