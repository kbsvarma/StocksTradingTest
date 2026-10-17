from pathlib import Path
import pandas as pd,hashlib,json
P=Path(__file__).resolve().parent;B=P.parent
r=pd.read_csv(P/'broker_replay.csv');tm=pd.read_csv(P/'timing_matched.csv');rn=pd.read_csv(P/'random_controls.csv');ra=pd.read_csv(P/'risk_adjusted.csv');su=pd.read_csv(P/'summary.csv');cv=pd.read_csv(P/'coverage.csv');mb=pd.read_csv(P/'merger_bracket.csv')
def table(x):return x.to_markdown(index=False,floatfmt='.2f')
main=r.query('strategy=="TopDecile" and capital==100000 and slip_bps==3')[['period','mode','days','cagr','dd_pct','net_dollars']]
scale=r.query('strategy=="TopDecile" and period=="2022+" and mode=="matched_0935" and slip_bps==3')[['capital','cagr','dd_pct']]
stress=r.query('strategy=="TopDecile" and capital==100000 and mode=="matched_0935"')[['period','slip_bps','cagr','dd_pct']]
report=f'''# Overnight stocks: corrected follow-through

September 12, 2026. This supersedes the first pilot's full-sample headlines. The pilot stopped before sufficient price-identity and execution validation; that was premature. This follow-through corrects identified defects and completes multi-year timing, broker-cost, random-control and risk-exposure checks using free and already archived data.

## Decision

**The ranking contains a historical return signal conditional on available data, but it has not established a stable, cost-efficient standalone strategy.** The top-decile basket beats count-matched random baskets on gross return. Much of its apparent attraction comes with materially greater market exposure. After commissions and modest execution friction, the 2022+ result is weak; delaying liquidation to 09:35 makes it negative at the $100k example size. The strong 2025+ period cannot override that longer result.

The regression-based OBG design is weaker. No change to Ridge or to the current paper bot is justified by these tests. No paid data was downloaded, no strategy thresholds were tuned, and no broker orders were placed.

## Data corrections made

1. Recovered SQ history through the verified same-company XYZ ticker change and ANTM through ELV. This adds history without replacing a purchased company with its acquirer. Sources: [Block's ticker-change announcement](https://www.nasdaq.com/press-release/block-announces-ticker-symbol-change-xyz-report-fourth-quarter-results-2025-01-09), [Elevance's rebranding announcement](https://www.elevancehealth.com/newsroom/elevance-health-rings-in-rebrand-with-nyse-opening-bell).
2. Invalidated zero-volume Open/Close observations in both training and replay. The prior daily cache included nontrading marks after acquisitions, including TESARO. Treating them as executable entries was incorrect. This is data cleaning, not a newly optimized trading rule. The model still assumes entry sizing at a recorded close; it does not simulate knowing the final closing print before the auction cutoff.
3. Found a **security-identity error in CZR** by comparing independent archived minute prices against daily history. Before July 21, 2020, Yahoo's current-CZR predecessor history and the historical-CZR archive represent different businesses. The change from ERI to CZR is documented in the [company's merger filing](https://www.sec.gov/Archives/edgar/data/1590895/000119312520196232/d940333d8k.htm). Quarantined the pre-change daily observations, including training windows; did not rescale them to fit.
4. Found a pre-July 2020 MTCH corporate-action normalization inconsistency. Quarantined that segment rather than guessing a split correction across the [IAC/Match separation](https://www.sec.gov/Archives/edgar/data/891103/000110465920080606/tm206790d25_8k.htm).
5. Rebuilt every monthly selection and replay after those corrections. These omissions remain explicit in coverage.csv; they increase uncertainty rather than silently creating a complete universe.

The pre-correction audit identified 92 CZR and 40 MTCH Universe stock-night opening-price discrepancies exceeding 20%. After correction, none exceeds that threshold among available matched Universe observations. This catches gross mapping/scale failures; it is not proof of auction accuracy or a complete corporate-action audit.

## Multi-year execution check

Used the archived XNAS morning minute data for 2018–2026, joining instrument identifiers by historical date and normalizing verified vendor split factors. Compared 09:30, 09:31, **09:35 (primary sensitivity)**, 09:45 and 10:00. These are venue-specific trade prints, not bid/ask quotes or guaranteed auction fills.

To compare timings fairly, all timing variants use the identical stock-night intersection. Missing allocations stay cash without redistributing their weight. This intersection is an ex-post coverage restriction, so the matched results are conditional diagnostics, not a deployable missing-data policy. Daily-data replay includes the final August 3, 2026 exit; archive matching ends with available July 29, 2026 exits.

{table(tm.query('strategy=="TopDecile" and timing in ["ret","m0935_ret"]'))}

About 26,000 matched stock-night observations now replace the earlier 176-pair recent execution check. The small-cache estimate of a roughly 26-bps timing penalty was not representative of this longer sample: the corrected 2022+ matched penalty is about **2 bps per stock round trip**, still material against a small net edge. Neither sample establishes actual slippage.

## Whole-share and broker-cost replay

Illustrative account sizes are $25k, $100k and $250k, to expose minimum-commission effects rather than impose a capital target. Each replay resets at the start of its reported period. Equal-weight intended allocations use whole shares, maintain a fee/friction reserve, and do not borrow. Equity compounds; these are not fixed-dollar-per-trade returns. The accounting assumes unsettled sale proceeds can be reused; it is not a settlement-aware cash-account simulation.

The fee scenario uses IBKR's currently published fixed rate of $0.005/share and $1/order minimum, with the notional cap and published sell-side regulatory charges. It is a constant present-day cost scenario across historical prices, not reconstructed historical invoices. Additional round-trip friction is 0, 3 or 10 bps. We model one execution per order, no partial-fill fee multiplication or actual impact. [IBKR published schedule](https://investors.interactivebrokers.com/en/pricing/commissions-stocks.php?re=amer).

The 3-bps column here is **additional to commissions**, unlike the original pilot's flat all-in 3-bps scenario. They must not be compared as if they were the same cost assumption. Routing-specific costs, pre-auction sizing uncertainty and imperfect fills remain outside this model. Dividends are accrued immediately, which simplifies actual dividend-payment cash timing.

### TopDecile: $100k starting value, commissions plus 3 bps

{table(main)}

- `daily_open`: all selected available daily histories, including missing-exit loss stress.
- `matched_daily`: daily opening returns only on the same archive-covered stock-night pairs used below; unavailable allocations remain cash.
- `matched_0935`: the identical pairs, exited at the archived 09:35 minute's first trade.

Compare `matched_daily` with `matched_0935` to isolate the price-timing difference. Comparing `daily_open` directly with a matched result also changes coverage and the treatment of missing exits. In particular, omitting the unresolved TESARO exit improves the matched full sample, so that improvement must not be credited to timing.

The 2022+ $100k matched 09:35 replay loses about $4,869 over 1,145 portfolio nights, with a 39.3% drawdown. The 2025+ replay earns about $23,365 over 392 nights, with a 31.7% drawdown. Neither implies predictable weekly income. Net dollars per individual stock round trip in those periods are approximately −$0.32 and +$4.43 respectively, at changing compounded equity and changing position sizes.

### Capital/commission scaling: 2022+, 09:35, commissions plus 3 bps

{table(scale)}

The $25k account suffers a feedback effect as losses make fixed minimum commissions larger relative to remaining capital. That is a cost-size result for this many daily tickets, not a recommendation to supply more capital. The larger-capital result still has substantial drawdown and excludes size-dependent impact.

### Additional friction sensitivity: $100k, matched 09:35

{table(stress)}

## Does selection add anything beyond random stocks?

Generated 500 independent count-matched random baskets, selecting without replacement from the same eligible stocks each month. Every simulated basket freezes membership monthly and uses the same daily return handling. No best random seed or threshold was selected.

{table(rn)}

None of the 500 random gross-return baskets exceeds TopDecile in these periods. That is evidence that the observed ranking result is not reproduced by merely selecting the same number of random names. It is **not** a p-value of zero, proof of alpha, or a cure for missing histories. The random baskets are count-matched, not volatility- or factor-matched.

## How much is compensation for market exposure?

As a separate diagnostic, regressed strategy returns after a flat 3-bps cost on Universe overnight returns and SPY close-to-close returns, with 20-lag HAC uncertainty intervals. These are retrospective exposure estimates, not a fitted trading strategy or broker-cost replay. They are one limited factor specification, not a claim to have controlled every risk.

{table(ra.query('strategy=="TopDecile" and cost_bps==3'))}

Every 95% alpha interval includes zero. TopDecile's estimated loading on the overnight Universe is roughly 1.6 in the full sample, 1.8 since 2022 and 2.4 since 2025. This makes the simple gross-return advantage less compelling as evidence of risk-adjusted efficiency. OBG's recent alpha is also unestablished; its complete results are retained in risk_adjusted.csv.

## Delisting outcome and remaining coverage

TESARO's January 2019 acquisition price was $75 cash per share; [GSK's offer announcement](https://www.gsk.com/en-gb/media/press-releases/gsk-reaches-agreement-to-acquire-tesaro-an-oncology-focused-biopharmaceutical-company/) and [completion announcement](https://us.gsk.com/en-us/media/press-releases/gsk-completes-acquisition-of-tesaro-an-oncology-focused-biopharmaceutical-company/) establish the terminal consideration and January 22 completion. The January 18 opening exit is absent from both inspected datasets. We do not fabricate a market fill.

The main daily replay retains a full-loss mark for that unresolved exit. A separate terminal-value bracket replaces it with the documented $75 consideration, with optimistically immediate recognition. This is an economic-value sensitivity, not a complete merger settlement/cash-lock replay:

{table(mb)}

The bracket uses the corrected fractional flat-3-bps baseline, **not** the whole-share broker table. Its full-sample annualized return changes by about 1.2 percentage points. It cannot explain away the weak 2022+ result because the event occurred in 2019. ESRX remains an unresolved Universe terminal exit and is disclosed in trades.parquet.

Missing-history stock-months remaining: {int((cv.reason=='missing_history').sum())}; short-history/quarantined stock-months: {int((cv.reason=='short_history').sum())}. Acquired and failed companies are still incompletely represented. Historical open/close prices for those missing companies cannot be reconstructed honestly from the morning-only venue archive. No independent complete daily auction archive was found locally, and no paid data was fetched.

## What is and is not complete

Completed: verified rename recovery; invalid-print removal; identifier/corporate-action cross-check; frozen-selection rerun; multi-year paired exit replay; whole-share and current-fee scenarios at three account sizes; 500 random controls; exposure-adjusted uncertainty; documented merger-value bracket. Five hundred sampled normalized exit calculations and portfolio uniqueness checks passed. Gross archive/daily price-scale inconsistencies were checked again after correction. Inputs, code and outputs are retained with hashes.

Not established by these data: survivorship-complete performance, exact auction participation/fills, a pristine forward advantage, or dependable dollar income. These are evidence gaps, not tuning opportunities. The research history cannot become a fresh holdout through another pass.

**Research disposition:** retain the frozen TopDecile definition as a candidate requiring complete stock histories and executable auction evidence. The tested implementation does not earn deployment or Ridge integration. OBG receives no promotion. The strongest useful finding is a return-ranking effect with high market exposure and a narrow cost budget, rather than the stable overnight edge originally hoped for.

Reproduction: from the repository root run phase2/backtest.py, phase2/archive_extract.py, phase2/followthrough.py, phase2/extra_checks.py, phase2/write_report.py using the full stock_overnight_2026_09 prefix. All data retrieval was free; these scripts otherwise use the saved archive. The root pilot files are preserved for comparison.
'''
(P/'REPORT.md').write_text(report)
hashes={str(f.relative_to(B)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file() and f.suffix in ['.py','.parquet','.csv','.md']};(P/'hashes.json').write_text(json.dumps(hashes,indent=2))
f=B/'REPORT.md';old=f.read_text();notice='> Superseded by [the corrected follow-through](phase2/REPORT.md). The original pilot contained identified security-history and execution-data defects; use the corrected report for conclusions.\n\n'
if not old.startswith('> Superseded'):f.write_text(notice+old)
print(P/'REPORT.md')
