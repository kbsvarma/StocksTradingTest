from pathlib import Path
import pandas as pd,json,numpy as np
P=Path(__file__).resolve().parent
S=pd.read_csv(P/'summary.csv');F=pd.read_csv(P/'fresh_start_periods.csv');A=pd.read_csv(P/'annual_returns.csv');R=pd.read_csv(P/'robustness.csv');K=pd.read_csv(P/'monthly_rankings.csv');T=pd.read_csv(P/'tax_sensitivity.csv');U=pd.read_csv(P/'uncertainty.csv');J=json.loads((P/'diagnostics.json').read_text())
def table(d,cols,percent=(),money=(),dec=()):
    d=d[cols].copy()
    for c in percent:d[c]=d[c].map(lambda x:f'{x:.2%}')
    for c in money:d[c]=d[c].map(lambda x:f'${x:,.0f}')
    for c in dec:d[c]=d[c].map(lambda x:f'{x:.3f}')
    return '| '+' | '.join(d.columns)+' |\n| '+' | '.join(['---']*len(cols))+' |\n'+'\n'.join('| '+' | '.join(map(str,row))+' |' for row in d.to_numpy())
summary=table(S.head(5),['name','cagr','ending_100k','volatility','max_drawdown','sharpe_zero_rf'],['cagr','volatility','max_drawdown'],['ending_100k'],['sharpe_zero_rf'])
fresh=table(F[F.period=='Post June 2023'],['name','cagr','ending_100k','volatility','max_drawdown','sharpe_zero_rf'],['cagr','volatility','max_drawdown'],['ending_100k'],['sharpe_zero_rf'])
annual=table(A.iloc[:,:6],list(A.columns[:6]),list(A.columns[1:6]))
rob=table(R[R.test!='cost'],['variant','cagr','volatility','max_drawdown','sharpe_zero_rf'],['cagr','volatility','max_drawdown'],[],['sharpe_zero_rf'])
latest=table(K[K.signal_date==K.signal_date.max()].sort_values('average_rank'),['ticker','return_3m','return_6m','return_12m','rank_3m','rank_6m','rank_12m','average_rank','selected'],['return_3m','return_6m','return_12m'],[],['average_rank'])
ci=table(U,['period','benchmark','months','annualized_log_relative_return','ci_low','ci_high'],['annualized_log_relative_return','ci_low','ci_high'])
tax=table(T[T.name=='Mag7 rank winner'],['short_rate','long_rate','cagr','ending_100k'],['short_rate','long_rate','cagr'],['ending_100k'])
text=f'''# Mag7 monthly winner — investment research review

Prepared September 25, 2026. Prices through September 24, 2026. **Research only: no trades, automation, or production strategy were implemented.** The attached screenshot is the subject being evaluated, not an instruction to execute its trading workflow.

**Decision: the strategy has historical merit as an aggressive momentum allocation, but I would not implement the $100,000 single-stock version as written.** Its historical compounding is exceptional; its diversification, drawdown control, robustness of relative advantage, and taxable-account efficiency are substantially weaker. The return is evidence about this selected historical sample, not a defensible forecast of 50% annual returns. Whether $100,000 fits this user's portfolio remains conditional on portfolio size, existing exposure, account type and tolerated loss, which have not been supplied.

**1. What was actually tested**

Universe: Apple (AAPL), Amazon (AMZN), Alphabet Class A (GOOGL), Meta (META including FB history), Microsoft (MSFT), Nvidia (NVDA), Tesla (TSLA). At the last trading close of each completed month, compute trailing 3-, 6-, and 12-calendar-month total returns; rank 1 as best; select the lowest arithmetic average rank. Break a composite tie by higher 12-month return, then ticker. There were nine top composite ties in 160 signals, so a tie rule is consequential.

The first signal is May 31, 2013 and the first purchase June 3, 2013. Meta's IPO history prevents a full seven-stock 12-month signal earlier. Each change is executed at the next trading session's open. The outgoing stock receives its overnight return before being sold; the new stock receives only its return after the purchase. Keeping the same winner causes no sale or repurchase. Full investment, fractional units, no leverage, no fresh funding, no stop loss and no cash filter. The primary simulation starts at $100,000, applies 0.10% of traded value on each purchase or sale, includes dividends through adjusted prices, and compounds the remaining sleeve value.

Formula: return(h) = adjusted close at month-end / adjusted close h month-ends earlier − 1. Score = (rank3 + rank6 + rank12) / 3. Rank averaging does not give three independent confirmations: the lookbacks overlap. It also discards the size of the return differences.

These rules fill gaps in the screenshot. The screenshot does not fix Alphabet's share class, the definition of a month, adjustment conventions, ties, dividend cash, approval deadline, missed approvals, tax funding, partial fills or the meaning of “restore the strategy sleeve.” The baseline uses a reinvested total-return convention; it is not a broker-share reconciliation. Cash-dividend sensitivity is below. No saved live signal or personal holdings were retrieved; the latest ranking is independently reconstructed from downloaded data.

**2. Full-history comparison: June 3, 2013–September 24, 2026**

{summary}

CAGR is compounded annual growth using actual elapsed calendar days. Volatility is daily standard deviation × √252. “sharpe_zero_rf” uses a zero risk-free rate, so it is a transparent return/volatility comparison, not an excess-over-Treasury Sharpe. Maximum drawdown uses daily closing values; intraday losses can be worse. Ending wealth is marked to market without terminal liquidation or taxes. Both equal-weight portfolios start at 1/7 per stock: one resets monthly, the other allows weights to drift. The latter becomes concentrated in its best performers.

The winner rule exceeds monthly equal-weight Mag7 CAGR by **13.77 percentage points**, but its annualized volatility is **47.62% versus 27.12%** and its zero-rate Sharpe is **1.096 versus 1.288**. It produces higher absolute wealth by taking a much rougher path. Buying NVDA throughout this retrospectively chosen sample returned 62.96% annualized with a 66.34% drawdown; that is a hindsight comparator, not a recommendation to buy it now.

![Historical wealth and daily drawdown](performance.png)

**3. The losses a holder had to survive**

- Maximum drawdown: **66.09%**, from November 4, 2021 to January 5, 2023. The modeled sleeve fell from approximately **$11,929,051 to $4,045,025** and recovered that peak on February 14, 2024. A $100,000 allocation made at an equivalent peak would fall to approximately **$33,909**. This is a rescaled historical loss, not the original $100,000 account's balance at that time.
- A 66.09% loss requires a **194.91% gain** to recover. The observed historical maximum is not a loss ceiling; one unlevered stock can lose nearly all its value.
- Longest period below a prior closing high: **659 trading sessions**, October 1, 2013–May 12, 2016. The 2021 peak-to-recovery episode was about 2.3 calendar years.
- Worst day: **−21.06%**, September 8, 2020. Worst calendar month: **−27.59%**, February 2025. Mean return on the worst 5% of daily observations: **−6.72%**.
- 2022 return: **−57.74%**, versus **−32.58% QQQ** and **−45.11% equal-weight Mag7**. In 2025, the strategy lost **28.49%** while QQQ gained **20.77%**.
- Through September 24, 2026, the strategy remained **25.51% below its December 2024 high**.
- Of **148 complete rolling 12-month windows**, **27.03% lost money**. It beat QQQ in **63.51%**, and monthly equal-weight Mag7 in only **54.05%**. These windows overlap; they are not independent trials or future probabilities.

The screenshot has no rule for avoiding a broad selloff: it selects the highest relative rank even when the candidates have poor absolute returns. Monthly decisions also cannot react promptly to earnings gaps or a fast reversal.

**4. Recent evidence with fresh capital**

June 1, 2023–September 24, 2026, each comparison restarted with $100,000. In particular, buy-and-hold Mag7 is reset to equal weights here; it does not inherit a large NVDA weight from 2013.

{fresh}

The strategy's recent return advantage over monthly equal weight is only **4.92 percentage points a year**, with a **55.02% drawdown versus 29.50%** and a lower Sharpe. This more recent window weakens the case that putting the entire sleeve in the top-ranked stock is worth its extra risk. June 2023 is a diagnostic split during the period when the Mag7 grouping gained attention, not a claim that these rules were actually preregistered then. There is **no genuine untouched forward test** of the proposed rule.

**5. How convincing is the statistical evidence?**

A paired circular block bootstrap resampled complete months in six-month blocks, 10,000 times, seed 20260925. It retains some serial dependence and compares the strategy with each benchmark on identical sampled months. The interval describes **annualized log relative return**, not CAGR percentage-point differences or forecast returns.

{ci}

All these 95% intervals include zero. In particular, the full-period interval relative to equal-weight Mag7 is **−9.90% to +30.65% annualized log relative return**. The result is consistent with useful historical momentum, but does not establish a dependable incremental edge over owning this exceptional universe. The bootstrap cannot correct retrospective universe selection, omitted failed stock groups, or research choices made before this review.

Among 10,000 random monthly single-stock selection paths drawn from these same seven historical winners, median CAGR was **29.23%**, with a 5th–95th percentile range of **15.19%–45.98%**. The rank rule exceeded **98.11%** of those paths. Giving random selectors the **same 45 switch dates** as the rank rule, with no consecutive repeated ticker, increased the random median to **30.70%**, and the rule exceeded **96.45%** of those paths. These are conditional historical comparisons, not p-values proving a deployable edge or probabilities of future success. The matched-switch dates themselves come from the realized strategy and are not a prospective random trading policy.

Academic evidence supports momentum across broader stock populations, but does not validate this precise seven-stock, one-position rule. The [original authors' momentum research](https://www.nber.org/papers/w7159) is useful context; evidence about diversified long–short momentum and its crash mechanism should not be mechanically transferred to this long-only sleeve.

**6. Concentration and dependence on particular winners**

NVDA was selected in **52/160 months (32.50%)**, and TSLA in **39/160 (24.38%)**. Together they account for **56.88%** of signal months. Under the disclosed daily log-return attribution convention, NVDA accounts for about **54.40%** of accumulated log growth; switch-day overnight returns are attributed to the newly selected ticker, so this is approximate attribution rather than exact trade contribution.

Excluding NVDA reduces CAGR from **50.37% to 31.27%**. Excluding TSLA raises CAGR to **56.30%** and reduces maximum drawdown to **44.61%**. Those exclusions re-rank the remaining six stocks and are sensitivity tests, not a proposal to pick the best historical exclusion. They demonstrate that inclusion of all seven, and especially winner-take-all allocation, is not independently justified by the headline backtest.

**7. Prespecified rule and execution sensitivities**

{rob}

The top-three variant returns **46.91%** annually with **32.44% volatility**, **51.12% drawdown**, and **1.351** zero-rate Sharpe. The original rule returns **50.37%** with **47.62% volatility**, **66.09% drawdown**, and **1.096** Sharpe. This is evidence against assuming that selecting only one stock is automatically optimal. Top three still has a severe historical drawdown. None of these variants has been selected for live use; changing the strategy after seeing these results requires new forward validation.

Signal-only price-return treatment reduces CAGR to **48.78%**; keeping dividends as cash until the next actual rotation produces **50.29%**. Thus dividends are not the main source of the claimed performance. The dividend cash illustration credits on ex-date rather than payable date, so it is not a full settlement ledger. GOOG substitution uses the vendor's mapped historical series, including pre-Class-C history; it is only a share-class sensitivity.

Trading at the next session's **close**, instead of its open, reduces CAGR to **46.20%**. Delaying both sides by one session gives **49.28%**. These differences show why “after I approve” needs a specific execution policy. A cash-settlement illustration that sells on the required day and waits until the following session to buy produces **46.41% CAGR and 61.69% drawdown**; that requires changing the screenshot's first-trading-day-only restriction and is not a valid execution of its current wording.

**8. Turnover, costs and taxes**

There were **45 stock changes over 13.31 years**, about **3.38 changes per year**, plus the initial purchase. This is 46 purchase events and 45 sale events. **114 of 159 subsequent monthly checks** retained the same winner. Average closed holding spell was **107.5 calendar days**; **43 of 45** closed spells lasted one year or less. Gross profitable closed spells were **31 of 45 (68.89%)**; this is distinct from the 57.50% fraction of positive calendar months.

Two-sided traded value averaged **6.83 times portfolio equity per year**, including initial entry. A complete $100,000 rotation costs about **$200 at 10 bps per side**, before intervening price movement. At 0 / 5 / 10 / 25 / 50 bps each side, strategy CAGRs are **51.40% / 50.88% / 50.37% / 48.83% / 46.31%**. A 10-bp assumption reduces CAGR by **1.03 percentage points** versus frictionless trading. Cost dollars across the enormous compounded historical account total about $610,146; that is not an estimate of annual costs for a fresh $100,000 sleeve.

A simplified tax sensitivity charges tax immediately at each realized gain, carries losses forward without refunds, applies the shown long/short holding-period rates and pays tax from the sleeve:

{tax}

These are **not fully after-tax performance estimates**. They use total-return units, omit dividend taxation, NIIT, state tax, wash-sale adjustments and cross-account netting, and leave the last open position untaxed. Immediate tax payment also differs from actual annual payment timing. QQQ/SPY buy-and-hold has no modeled interim capital-gain realization, with its terminal tax deferred too. The direction is meaningful—frequent realization reduces compounding—but personal tax outcomes cannot be calculated without the user's actual lots and circumstances. Paying taxes with outside money would violate a strict all-in self-financing interpretation unless explicitly allowed.

The IRS wash-sale window includes acquisitions within 30 days before or after a loss sale. Activity in preexisting holdings, dividend reinvestment or other relevant accounts can interact with strategy trades; an internal sleeve label does not create a separate tax boundary. [IRS Publication 550](https://www.irs.gov/publications/p550).

**9. Portfolio fit and the $100,000 amount**

Daily correlation with QQQ is **0.623**, with a simple zero-risk-free regression beta of **1.409**. Against SPY, correlation is **0.543** and beta **1.530**. Correlation below one does not make this a defensive hedge: it remains concentrated equity exposure, and it increases volatility in the tested portfolio combinations.

If $100,000 is part of total investable assets, a repeat of the 66.09% sleeve drawdown has the following direct effect, holding the rest of the portfolio flat:

| Total portfolio at allocation | Sleeve weight | Sleeve loss | Direct total-portfolio loss |
| --- | --- | --- | --- |
| $250,000 | 40% | $66,091 | 26.44% |
| $500,000 | 20% | $66,091 | 13.22% |
| $1,000,000 | 10% | $66,091 | 6.61% |
| $2,000,000 | 5% | $66,091 | 3.30% |

The rest of the portfolio is unlikely to be flat in a correlated equity selloff. Existing NVDA/TSLA/Mag7 shares and embedded exposure through QQQ or other funds add to the same economic risk, even if none of those shares is sold. If a $20,000–$30,000 loss on this sleeve is unacceptable, the proposed $100,000 position does not fit the observed history. If the goal is dependable capital preservation or diversification away from technology, this rule is unsuitable for that goal.

Position budget formula: sleeve dollars ≤ tolerable portfolio-dollar loss / assumed sleeve-loss fraction. For a $1m portfolio and a 5% loss budget attributable to this sleeve, the historical 66.09% stress gives approximately **$75,653**, while a total-loss stress gives **$50,000**. Neither is a recommended allocation; the historical loss is not a maximum future loss.

**The sleeve is not capped at $100,000.** Its equity compounds. In the historical simulation, starting a $100,000 sleeve alongside $900,000 of QQQ without transfers would take the sleeve from **10% to 69.19%** of combined wealth, with a maximum weight of **80.81%**. Combined daily-close drawdown was **56.14%**, versus QQQ's **35.12%**. A constant-10% model would require periodically transferring money into or out of the sleeve and is incompatible with strict “no new outside capital” after losses. The constant-weight diagnostic is saved separately and should not be mistaken for the screenshot's policy. A future exposure ceiling and treatment of profits need an explicit decision.

**10. Brokerage feasibility of the written safeguards**

Robinhood currently supports selecting specific lots, and its default disposal method is FIFO. Selling the number of shares recorded in an internal strategy ledger does not by itself guarantee that the original lots are preserved. A production design must select and verify actual strategy lots, reconcile splits and fractional quantities, and handle competing orders that could consume those lots. Robinhood describes circumstances where conflicting orders can cause fallback to default disposal. Tool/API support for lot selection has **not** been verified in this review. [Robinhood tax lots](https://robinhood.com/us/en/support/articles/tax-lots/).

Robinhood says margin accounts can trade immediately using unsettled stock-sale proceeds, while cash accounts must wait one trading day. Hence same-day full rotation using only sleeve proceeds is account-type dependent. No assumption of margin borrowing is required for the model, but settlement buying power must actually exist. [Robinhood settlement and buying power](https://robinhood.com/us/en/support/articles/360001226946/).

Exact buy quantities cannot be known from uncertain future sale proceeds and fill prices. A future order proposal needs either a dollar budget with permitted price limits or a second calculation after the sale fills, with clearly defined approval scope. A missed approval must not quietly become a late trade. Partial fills, halted stocks, rejected orders and uncertain submission status need explicit state handling. A database entry recording intent cannot substitute for verified fills or lot identity.

These operational protections have merit. They protect the rest of the account from accidental interference, but do not improve the strategy's expected return or cap its investment loss. No broker connection, order entry, automation or deployment was performed.

**11. Most recent completed month-end ranking, for audit only**

Signal close: **August 31, 2026**; modeled execution date: **September 1, 2026**. Returns below are total returns, not unadjusted price changes. This is a reconstructed research signal, not a saved signal retrieved from the user's account and not an order proposal. September 2026 is incomplete and was not ranked as a completed month.

{latest}

The reconstructed winner is NVDA. That fact is separate from the investment decision about adopting the rule.

**12. Calendar-year results**

{annual}

2013 covers June 3 onward; 2026 stops September 24. These are actual compounded period returns, not annualized partial-year values. Other years are full calendar-year strategy returns including overnight returns on the first session while a position was already held. Fresh-start subperiods are reported separately.

**13. Data quality, verification and reproducibility**

Downloaded Yahoo Finance daily history via yfinance 1.3.0 on September 25, 2026; input period begins January 2012. All ten series end September 24, 2026. There are **3,349 valuation sessions** in the primary simulation and no missing required observations in that common trading period. Meta's 95 missing earlier SPY sessions precede its IPO. The SPY session index supplies the observed trading calendar; no prices were forward-filled. All monthly decisions use completed prior closes. The last incomplete calendar month is excluded from rolling complete-month windows and bootstrap estimates.

For the nine available overlapping cached symbols, 2,436 raw closing-price observations per symbol exactly matched the earlier local-project cache. Maximum revised daily adjusted-return differences were about 0.011 basis points. This is a reproducibility check of the same provider, **not independent vendor validation**. Reconstructing returns from split-adjusted closes plus dividends showed no discrepancies above 10 basis points, and the largest was about 2.62 basis points. Corporate actions and large moves are exported for audit.

An independently coded one-stock recurrence reproduced the primary share-ledger curve to a maximum relative difference of **5.71 × 10⁻¹³**. Assertions check signal-before-execution ordering, positive finite prices and switch counts. This validates the modeled arithmetic; it does not certify execution prices, tax reporting or upstream price accuracy. The primary feed, historical universe selection and opening-fill assumptions remain material limitations. No paid data was acquired.

The opening-price model is a reference simulation. A user approving after the market opens cannot retroactively receive that price. Slippage is an assumption, not a measured quote-level estimate; capacity after the historical sleeve grows into tens of millions has not been independently established. Exact output decimals mean exact calculations on the saved inputs, not economic certainty.

Reproduce from this folder with Python 3.12.3, numpy 2.1.3, pandas 2.2.3, matplotlib 3.10.0: `python analyze.py`, `python supplement.py`, `python write_report.py`. `download.py` refreshes data and may change adjusted-price decimals; use saved inputs for exact reproduction. Research also ran under the repository's resource-limited remote runner. SHA256 input hashes and retrieval times are in `data_manifest.json`; analysis script hash and validation outputs are in `diagnostics.json`.

Primary artifacts: `PROTOCOL.md`, `summary.csv`, `daily_returns.csv`, `monthly_rankings.csv`, `rotations.csv`, `holding_spells.csv`, `fresh_start_periods.csv`, `annual_returns.csv`, `robustness.csv`, `rolling.csv`, `uncertainty.csv`, `attribution.csv`, `tax_sensitivity.csv`, `no_topup_portfolio_fit.csv`, `adjustment_audit.csv`, and `performance.png`. `periods.csv` shows slices of continuing 2013 portfolios; use `fresh_start_periods.csv` when comparing fresh allocations. All prices are retained in `data/`.

**What would change the decision:** personal exposure/loss-budget information could establish whether a small speculative allocation is acceptable. Evidence for live implementation would also require a frozen rule and prospective paper execution, a policy for exposure growth and approval delays, and verified broker lot/settlement capabilities. The current historical study supports continued research, not committing $100,000 to the unmodified wording.
'''
(P/'REPORT.md').write_text(text)
print('Wrote report',len(text.split()),'words')
