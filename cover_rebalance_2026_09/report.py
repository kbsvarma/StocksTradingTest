from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
P=Path(__file__).resolve().parent
s=pd.read_csv(P/'summary.csv');d=pd.read_parquet(P/'daily.parquet');info=json.loads((P/'details.json').read_text());sel=json.loads((P/'selected_pair.json').read_text());inv=json.loads((P/'inventory_summary.json').read_text());verification=json.loads((P/'verification.json').read_text())
labels={'AMD_buy_hold':'AMD buy-and-hold','NKTR_buy_hold':'NKTR buy-and-hold','pair_buy_hold_50_50':'50/50 buy-and-hold','daily_rebalance_50_50':'Daily 50/50 rebalance','cover_universal_1001':'Cover universal mixture','hindsight_optimal_CRPortfolio':'Best constant weight in hindsight'}
keys=list(labels)
def view(bps):
 q=s[s.cost_bps==bps].set_index('strategy').loc[keys].reset_index();q['Strategy']=q.strategy.map(labels);q['End wealth']=q.terminal_wealth.map(lambda v:f'${v:,.0f}');q['Multiple']=q.multiple.map(lambda v:f'{v:.3f}x');q['CAGR']=q.cagr.map(lambda v:f'{v:.2%}');q['Max drawdown']=q.max_drawdown.map(lambda v:f'{v:.2%}')
 return q[['Strategy','End wealth','Multiple','CAGR','Max drawdown']].to_markdown(index=False)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
fig,(ax,ddax)=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
colors={'AMD_buy_hold':'#94a3b8','NKTR_buy_hold':'#bd6a72','pair_buy_hold_50_50':'#2563eb','daily_rebalance_50_50':'#d97706','cover_universal_1001':'#059669'}
for model,color in colors.items():
 q=d[(d.strategy==model)&(d.cost_bps==0)];dates=pd.DatetimeIndex([pd.Timestamp(info['start'])]).append(pd.DatetimeIndex(q.date));wealth=np.r_[100000,q.wealth]
 ax.plot(dates,wealth,label=labels[model],color=color,lw=1.8);ddax.plot(dates,(wealth/np.maximum.accumulate(wealth)-1)*100,color=color,lw=1.3)
ax.set_yscale('log');ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'${x/1e6:.1f}M' if x>=1e6 else f'${x/1000:g}k'));ax.set_ylabel('Wealth, log scale');ax.legend(loc='upper left',ncol=2,frameon=False,fontsize=9);ax.set_title('AMD + NKTR: selected from pre-June-2018 volatility and correlation',loc='left',fontweight='bold')
ddax.set_ylabel('Drawdown (%)');ddax.set_ylim(-105,5)
for a in [ax,ddax]:a.grid(True,alpha=.18);a.spines[['top','right']].set_visible(False)
fig.text(.08,.015,'$100,000 initial capital | Gross theoretical daily-close returns | No pair switching | Through 11 Sep 2026',fontsize=9,color='#475569');fig.tight_layout(rect=[0,.03,1,1]);fig.savefig(P/'wealth_and_drawdowns.png',dpi=160);plt.close(fig)
annual=pd.read_csv(P/'annual.csv');annual=annual[annual.cost_bps==0].pivot(index='year',columns='strategy',values='return_net')
annual=annual[['pair_buy_hold_50_50','daily_rebalance_50_50','cover_universal_1001']];annual.columns=[labels[c] for c in annual.columns];annual=annual.map(lambda v:f'{v:.2%}').reset_index()
text=f'''# Frozen-pair test: AMD and NKTR

**This pair did not reproduce the Iroquois/Kin Ark setting: AMD became a persistent strong winner while NKTR lost most of its value.**
The test establishes that high volatility and weak correlation alone were insufficient
for this pair. It does not test the narrower hypothesis of harvesting fluctuations
from two stocks with modest long-run growth, and should not be presented as a
rejection of that hypothesis.
Starting with $100,000 on May 31, 2018, daily 50/50 rebalancing ended at $426,782
before costs (19.15% CAGR), versus $1,882,467 for simply holding the original
50/50 allocation (42.53% CAGR). The standard universal mixture reached $964,003
(31.47% CAGR). No second pair was tested to rescue the result.

## Correction to the interpretation
The original selection rule matched high volatility and low correlation but did
not match the historical example's modest constituent growth. Comparing the
selected pair with buy-and-hold is valid for that pair, but extrapolating its
failure to the user's intended setting was too broad.

AMD was selected from information through May 2018, before its later AI-era
performance. Excluding it specifically because of that subsequent outcome would
be hindsight. Two distinct questions should therefore be separated:

1. A retrospective mechanism demonstration: identify stocks whose realized
   long-run returns were modest and ask whether daily rebalancing greatly
   improved the outcome. This may faithfully resemble the historical example,
   but its selection uses hindsight and cannot establish prospective tradability.
2. A prospective strategy test: define observable business/universe restrictions
   and past-only return-path criteria before the evaluation period, select and
   freeze the pair, and retain failures. This tests whether that setting can be
   identified before its future returns are known.

This correction changes interpretation only. Pair selection, prices, cashflows,
and all reported numerical results remain unchanged. Neither of the two narrower
experiments above has been completed in this folder.

## What was actually searched
The scan inventoried {inv['files']:,} data-format files ({inv['bytes']/1e9:.2f} GB)
under the local GitHub projects and Downloads. Only Macintosh HD was mounted.
This includes copies and derived files; it is not a count of independent datasets.
Folder-level representative Parquet schemas distinguish option quotes, intraday
bars, futures, adjusted equities, and derived data. It does not imply every row
in all 105,590 files was read or validated.

The broad daily-equity cache has 458 Parquet files, adjusted closes, and action
fields. An archived June 2018 point-in-time liquidity universe enables historical
selection; fixed modern ticker lists and futures/option data were not substituted.
Selection used {sel['eligible_stocks']} eligible common stocks from {sel['universe_stocks']}
archived symbols. Exclusions: {sel['exclusion_counts']}. Missing historical companies
leave survivorship/availability bias even with historical universe membership.

## Pair chosen before evaluating later returns
Selection period: January 2017 through May 31, 2018; 354 paired daily returns.
High volatility meant the top quartile (cutoff {sel['vol_top_quartile_cutoff']:.2%}
annualized); weak correlation meant <=0.20. Among {sel['high_vol_stocks']} high-volatility
stocks and {sel['qualifying_pairs']} qualifying pairs, select the largest training-period
variance of relative log returns divided by eight. This is a diversification-growth
proxy, not a fitted return forecast or a prediction of outperformance over buy-and-hold.
These screening definitions operationalize the user's description; Cover's theorem
does not prescribe them. All training rankings are saved, but their later returns
were not scanned for a winner.

| | AMD | NKTR |
|---|---:|---:|
| Selection-period annualized volatility | {sel['pair']['vol_a']:.2%} | {sel['pair']['vol_b']:.2%} |
| Evaluation-period annualized volatility | {info['realized_vol']['AMD']:.2%} | {info['realized_vol']['NKTR']:.2%} |

Correlation was {sel['pair']['correlation']:.3f} during selection and
{info['realized_correlation']:.3f} during evaluation. Thus high volatility and weak
correlation persisted; failure to beat buy-and-hold cannot simply be attributed
to those selection characteristics disappearing.

The selected pair and source hashes were saved before its post-selection backtest.
The universe's construction uses the preceding month's liquidity, verified in
opening_leader_2026_07/build_universe.py. No daily stops, leverage, bands, monthly
rebalancing, pair changes, or adaptive directional predictors were introduced.

## Results: theory as a frictionless daily rebalancing rule
{info['days']:,} daily return periods, {info['start']} to {info['end']};
{info['years']:.3f} calendar years. Fractional shares; no contributions or withdrawals.
All price returns use the same split/dividend-adjusted daily series. The universal
strategy is the standard wealth-weighted mixture of constant-rebalanced experts,
using 1,001 weights and trapezoid quadrature of the uniform prior. It is not the
video's later short-enabled finite-horizon minimax extension.

{view(0)}

The hindsight optimum is **100% AMD**, at the endpoint of the allowed constant-weight
range. In this sample, the best constant-rebalanced portfolio does not benefit
from holding both stocks. It is labeled an oracle because we did not know that in
May 2018.

![Wealth and drawdowns](wealth_and_drawdowns.png)

## Costs on actual traded dollars
Five basis points per dollar bought or sold, including initial purchase and final
liquidation. This is a research sensitivity, not a verified broker execution cost.
Net target weights are solved after fees; the full account is not charged every
day. Universal costs apply to aggregate trades, not every internal expert.

{view(5)}

Daily 50/50 turnover was approximately 5.16 times account value per year including
both buys and sells and initial/final transactions. At 10 basis points per traded
dollar its CAGR was 18.54%. The gross strategy already underperformed, so costs
are not the main explanation. Exact daily-close fills and taxes are not modeled.

## Why a real diversification benefit still lost to buy-and-hold
For 50/50, the exact daily identity is:

    log((R_A + R_B)/2)
      = 0.5 log(R_A) + 0.5 log(R_B)
        + [log((R_A + R_B)/2) - 0.5 log(R_A) - 0.5 log(R_B)].

The bracketed diversification contribution is nonnegative. In this sample:

- Weighted constituent log growth: **{100*info['weighted_constituent_log_growth_per_year']:.2f}% per year**.
- Additional diversification contribution: **{100*info['diversification_log_growth_per_year']:.2f}% per year**.
- Portfolio log growth: **{100*info['crp_log_growth_per_year']:.2f}% per year**, equivalent to 19.15% CAGR.

These log-growth percentages add; ordinary CAGRs do not. The mechanism exists and
worked algebraically. However, it compares the portfolio with the weighted
geometric constituent growth, not with a buy-and-hold portfolio whose weights
are allowed to drift. AMD multiplied approximately 37.59x while NKTR retained
about 5.79% of its initial value. Fixed rebalancing continually replenished NKTR;
buy-and-hold increasingly concentrated in AMD. Daily rebalancing also suffered
about an 80.45% peak-to-trough drawdown, versus 65.48% for the same-pair buy-and-hold.

## Comparison with the video/paper
The paper's 73.619x over 22 years corresponds to approximately
**{info['paper_example_cagr']:.2%} CAGR**. Our daily 50/50 CAGR of 19.15% is broadly
similar in annualized magnitude, but its economic comparison is very different:
it underperformed the same-pair buy-and-hold by roughly 23.38 percentage points
of CAGR. Its terminal wealth was only 22.67% of that comparator.

Cover's example is not a promised return range. Nor does the universal theorem
promise finite-horizon equality in wealth or solve pair selection. The universal
mixture's 31.47% CAGR and 9.64x wealth do not establish an edge when the matched
buy-and-hold produced 18.82x. No 22-year wealth is extrapolated from our shorter
sample. Reference: [Cover, Universal Portfolios (1991)](https://isl.stanford.edu/~cover/papers/paper93.pdf).

## Annual returns
2018 and 2026 are partial periods. These are the unmodified strategies, not new
models selected from their annual performance.

{annual.to_markdown(index=False)}

## Data checks and validation
- No missing selected-pair prices across the requested 2,081 daily return periods;
  no truncation or pair substitution.
- Neither series reports cash dividends over this evaluation window. NKTR's
  June 9, 2025 1-for-15 reverse split is present in the action fields; adjusted
  prices avoid treating the nominal share consolidation as an investment gain.
- No >50-basis-point discrepancies between adjusted-close returns and the same
  vendor's Close-plus-Dividends convention. This is internal consistency, not
  independent vendor verification.
- Independently reconciled **16 complete share-ledger curves** against daily
  portfolio calculations; maximum dollar discrepancy below $0.000001.
- Reproduced the lecture's cash/doubling-or-halving-stock geometric multiplier
  sqrt(1.125)=1.060660; tested the equal-ending-price and persistent-winner examples.
- Verified universal wealth equals the integrated wealth of its constant-weight
  experts. Increasing grid resolution from 1,001 to 10,001 changed terminal
  wealth by only {abs(info['universal_grid_terminal_relative_error'])*100:.6f}%.
- Verified the hindsight endpoint optimum using an independent 10,001-weight grid,
  fixed daily target weights, and cost monotonicity.

This is one historically selected pair with incomplete historical-universe data,
not a statistical proof that all rebalancing strategies fail. It also does not
validate a repeatable edge. The next legitimate research question would concern
asset selection and replication on predeclared additional periods/universes;
changing pairs until the outcome looks good would not resolve it.

## Files and reproduction
PROTOCOL.md records rules before pair selection. selected_pair.json freezes the
pair and source hashes. pair_training_rankings.csv contains training scores only.
summary.csv, annual.csv, daily.parquet, and adjusted_prices.csv retain the results.
verification.json records independent checks. No existing data or live strategy
files were changed and no additional market data were purchased.

    /opt/anaconda3/bin/python cover_rebalance_2026_09/inventory.py
    /opt/anaconda3/bin/python cover_rebalance_2026_09/select_pair.py
    /opt/anaconda3/bin/python cover_rebalance_2026_09/backtest.py
    /opt/anaconda3/bin/python cover_rebalance_2026_09/verify.py
    /opt/anaconda3/bin/python cover_rebalance_2026_09/report.py
'''
(P/'REPORT.md').write_text(text);print(P/'REPORT.md')
