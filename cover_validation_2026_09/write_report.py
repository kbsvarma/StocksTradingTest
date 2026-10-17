from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
low=pd.read_csv(P/'VALIDATED_LOWER_ALL.csv');high=pd.read_csv(P/'AUDITED_HIGHER_TOP10_ALL.csv');highna=pd.read_csv(P/'AUDITED_HIGHER_TOP10_NON_AI.csv');details=pd.read_csv(P/'long_period_checks.csv');exe=pd.read_csv(P/'execution_results.csv');counts=json.loads((P/'DECISION_FILTER_COUNTS.json').read_text());preferred=[('MEDP','PGR'),('ALLE','KR'),('ARES','COST'),('AMGN','CBOE'),('CBOE','KR')]
def pct(x):return f'{x*100:.1f}%'
def table(head,rows):return '| '+' | '.join(head)+' |\n| '+' | '.join(['---']*len(head))+' |\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)
def link(file,label=None):return f'[{label or file}]({P/file})'
def lowtable(d):return table(['Rank','Pair','Years','Daily CAGR','Hold CAGR','Worst drawdown'],[[i+1,f'{r.a}/{r.b}',f'{r.long_10_years:.1f}',pct(r.long_10_cagr),pct(r.long_10_hold_cagr),pct(-r.long_10_maxdd)] for i,(_,r) in enumerate(d.iterrows())])
def hightable(d):return table(['Rank','Pair','Years','Daily CAGR','Hold CAGR','Worst drawdown','2-year starts beating hold'],[[i+1,f'{r.a}/{r.b}',f'{r.long_10_years:.1f}',pct(r.long_10_cagr),pct(r.long_10_hold_cagr),pct(-r.long_10_maxdd),pct(r.roll_2_beat_hold_fraction)] for i,(_,r) in enumerate(d.iterrows())])
five=pd.DataFrame([low[(low.a==a)&(low.b==b)].iloc[0] for a,b in preferred]);five.to_csv(P/'PREFERRED_FIVE.csv',index=False)
pt=table(['Pair','Common history starts','Daily CAGR','Hold CAGR','CAGR uplift','Worst DD: daily / hold','Beats both stocks?'],[[f'{r.a}/{r.b}',r.long_10_start,pct(r.long_10_cagr),pct(r.long_10_hold_cagr),f'{100*(r.long_10_cagr-r.long_10_hold_cagr):.2f} pp',f'{pct(-r.long_10_maxdd)} / {pct(-r.long_10_hold_maxdd)}','Yes' if r.long_10_beats_both else 'No'] for _,r in five.iterrows()])
et=table(['Pair','2022–25 daily CAGR','Matched hold CAGR','2018+ daily / hold CAGR','2026 return: daily / hold'],[[f'{r.a}/{r.b}',pct(r.exec_discovery_cagr),pct(r.exec_discovery_hold_cagr),f'{pct(r.exec_vendor_full_cagr)} / {pct(r.exec_vendor_full_hold_cagr)}',f'{pct((1+r.exec_confirmation_cagr)**r.exec_confirmation_years-1)} / {pct((1+r.exec_confirmation_hold_cagr)**r.exec_confirmation_years-1)}'] for _,r in five.iterrows()])
recs=[]
for pair in [('COST','HALO'),('HALO','MNST'),('MSTR','TDS')]:
 z=details[(details.a==pair[0])&(details.b==pair[1])&(details.period=='22year')&(details.cost_bps==10)]
 if len(z):
  r=z.iloc[0];recs.append([f'{r.a}/{r.b}',r.start,f'{r.terminal_1000/1000:.1f}×',pct(r.cagr),pct(r.hold_cagr),pct(-r.maxdd)])
charges=sum(x['quote_usd'] for f in P.glob('SPEND_*.json') for x in json.loads(f.read_text()));reserved=sum(x['reserved_usd'] for f in P.glob('SPEND_*.json') for x in json.loads(f.read_text()))
text=f'''**Daily two-stock rebalancing — completed Phases 1 and 2**  
Data through September 11, 2026. Fixed 50/50, daily, long-only, no leverage.

**My stance: continue with the original design. MEDP/PGR is my first choice for a controlled execution trial; ALLE/KR is the next choice.** The historical rebalancing benefit survives independent price checks and a substantially more realistic execution model. I would keep ARES/COST, AMGN/CBOE and CBOE/KR as additional candidates. These five are alternatives for choosing a pair; combined portfolio construction belongs to later work.

The safer version produces a much smaller incremental benefit than the spectacular examples: roughly 1–3 percentage points of additional annualized return for these preferred pairs. Much of the total return still comes from owning the businesses. The high-return speculative pairs retain a historical rebalancing benefit, but their 60–90% drawdowns make them unsuitable for your lower-risk objective. **I would not use SMCI/VKTX as the lower-risk version.** Phase 3 has not been started.

**What was actually covered**

- Classified all **23,567 historical instrument symbols** in the raw local panel, including historical names absent from current listings.
- Accounted for **9,059 original/recovered stock identifiers**. Missing histories, illiquidity, corporate-action flags, funds and identity mismatches are retained in the ledgers.
- Final company-stock universe: **1,907 stocks, all 1,817,371 unordered pairs**. The computed superset contained 1,836,486 pairs, including nine funds and one quarantined duplicate that were excluded from company rankings.
- Extended the entire eligible universe back to 2000 where histories exist; four unavailable refreshes retain their original histories. Repeated the full pair screen and monthly-entry 1/2/3-year tests. Longer 5/10-year entry tests were run on the verification pool.
- Independently examined **354 pairs / 218 stocks**, covering every one of the 135 longer-history lower-risk qualifiers and the broader speculative shortlist. Ran **6,372 execution scenarios** across three periods, three extra-cost levels and two dividend-cash lags.

The longer-history check reduced the lower-risk qualifying set from 1,592 to **135**. After the decision gates below, **{len(low)} pairs** remain. All are outside your excluded holdings/AI-infrastructure universe. The unrestricted and non-AI lower-risk top-ten lists therefore coincide; this is a result of the tests, not a restriction imposed on the unrestricted search.

**Five candidates I would prioritize**

CAGR means compound annual growth rate. This table uses the longest common history available since 2000 through September 11, 2026, daily closing-price rebalancing, dividends reinvested, and 10 basis points on traded value. “Hold” starts with the same 50/50 investment and never transfers money between the stocks. Returns are pre-tax. Drawdown measures loss from an earlier portfolio peak.

{pt}

MEDP/PGR offers the strongest return evidence of this group. It beat matched buy-and-hold in 91.8% of tested two-year monthly-entry windows and every tested five-year window; the windows overlap and are not independent forecasts. It did **not** beat MEDP alone over the full period. ALLE/KR is a clearer example of beating both constituents, with a smaller observed drawdown. AMGN/CBOE had a slightly worse drawdown than its holding benchmark; rebalancing does not automatically improve every risk statistic.

These preferred pairs have approximately 10–16 years of common history. Their records include COVID and 2022, but none provides a 22-year two-stock record or a joint 2008 test. Extending older pairs to the financial crisis was precisely what disqualified many apparently safer recent-history choices.

The financial gate checks current size, revenue, profitability, return on equity and cash generation. It is a screening proxy, not a credit rating. Primary issuer checks support that these are operating businesses: [Allegion’s Q2 2026 release](https://investor.allegion.com/news-and-events/news-releases/2026/07-23-2026-110034749) reports profitable security-products operations; [Cboe’s Q2 2026 results](https://ir.cboe.com/financials/quarterly-results/default.aspx) document its exchange business. [Medpace’s issuer FAQ](https://investor.medpace.com/investor-faqs) establishes its August 2016 trading debut. Full financial screening inputs are in {link('company_quality_audit.csv')}.

**The ranked lower-risk top ten — both unrestricted and excluding your AI list**

This ordering retains the original robustness score after the longer-history risk and verification gates. It is deliberately shown without hiding repeated names.

{lowtable(low.head(10))}

Eight rows share YUMC. They largely represent the same underlying exposure and add substantial China-business concentration. **I would choose among the preferred alternatives above rather than treat these ten rows as ten independent strategies.** The complete {len(low)}-pair candidate set and every rejected lower-risk pair remain available in {link('VALIDATED_LOWER_ALL.csv','the surviving candidate table')} and {link('LOWER_VALIDATION_ALL.csv','the complete lower-risk decision audit')}.

**The ten highest-ranked speculative pairs, with the longer-history risk exposed**

The original discovery-score ordering is preserved. The table uses the same 10-basis-point closing-price model and longest common histories; different inception dates mean these are not identical market periods. All ten show a positive historical holding-relative benefit that survives the costed discovery-period execution check. Their large losses are part of the result.

{hightable(high)}

SMCI/VKTX is a legitimate historical rebalancing candidate in the mathematical sense: approximately 36.7% annualized versus 21.0% for holding over its available history. Its roughly 69.4% drawdown and inconsistent two-year entry results are the reasons to reject it for the lower-risk objective. It also overlaps the AI-server exposure you wanted to separate. BTU/VKTX beat holding in fewer than half its tested two-year entry windows despite a strong discovery-period headline. Several rows share VKTX; they are not independent bets.

The non-AI speculative top ten is separately supplied in {link('AUDITED_HIGHER_TOP10_NON_AI.csv')}. This exclusion list covers your named holdings and identified AI infrastructure exposures; it does not mean every remaining business has zero connection to AI or every speculative stock is outside a hype cycle. Full speculative validation results are in {link('HIGHER_VALIDATION_ALL.csv')}.

**What happened when trading costs and execution constraints were added**

The following uses actual Nasdaq-venue bid/ask observations: the target is determined two minutes before the session close and traded one minute before the close, with early closes handled explicitly. The model adds 10 basis points beyond the observed spread, caps orders at displayed size, carries partial fills, checks trading status to avoid halted-market fills, and sweeps up to ten displayed bid levels for terminal liquidation where necessary. Dividend cash becomes spendable after a modeled 30-calendar-day lag; the zero-lag sensitivity is also retained. Both strategies receive the same entry/exit and reinvestment treatment.

{et}

The 2018+ columns begin May 1, 2018 for these five pairs. The 2026 column is the actual partial-year return, not an annual forecast. The full 2018+ MEDP/PGR comparison is approximately 32.7% versus 31.6% annualized: the incremental benefit is noticeably smaller than in 2022–25, and that weaker comparison is included in the recommendation.

For MEDP/PGR, the discovery-period median trade was about $5.71 in the $1,000-start simulation. There were roughly two orders per trading day. **At this capital size, the design requires a broker that supports the necessary fractional orders with no fixed per-order commission.** A $1 commission on roughly 500 orders per year would be about $500 in annual fees before compounding effects. Taxes and broker-specific fractional-share execution rules are not included in these returns. No trades have been placed.

Nasdaq venue quotes are not consolidated NBBO. These are modeled executions, not recorded broker fills. The partial-fill counts, weight deviations, halt/missing-data counts, depth costs and period dates are exposed in {link('execution_results.csv')}; they were not silently removed. Some speculative histories have older OTC or pre-combination coverage limitations, so their discovery-period verification must not be described as an independently executable simulation of every earlier year.

**How this compares with the Cover example**

Cover’s own paper reports 8.9151× and 4.1276× for the stocks, 73.619× for the best hindsight 55/45 fixed-weight portfolio, and 38.6727× for the universal algorithm. The 73.619× figure over 22 years is approximately **21.6% annualized**, not 70% per year. Our rule stays fixed at 50/50 and does not optimize weights after observing the outcome. [Cover’s paper](https://isl.stanford.edu/~cover/papers/paper93.pdf)

There are examples in the screened/verified pool that reach that compounded-return scale in the 22-year window:

{table(['Pair','Window starts','Ending multiple','Daily CAGR','Hold CAGR','Worst drawdown'],recs)}

Those rows are scale comparisons, not replacements selected to make the headline look better. Older prices before Databento’s coverage remain sourced from the free history. COST/HALO is a useful higher-risk non-AI reference with a much longer record, but its roughly 49% drawdown exceeds the lower-risk limit, and it underperformed holding in the 2026 execution diagnostic. Matching a terminal multiple does not match the risk or establish that the same outcome will repeat.

{link('compounding_and_drawdown.png','View the wealth and drawdown plots')}

**Decision rules, failures and evidential limits**

The lower-risk screen required current financial-quality proxies, different sectors, discovery correlation no greater than 0.5, at least eight years through 2025, positive return and holding-relative benefit in both discovery halves, and no worse than a 30% observed pre-2026 drawdown. Ranking uses the weaker discovery-half annual log benefit relative to that drawdown.

The final practical gates additionally require at least 1% annual log growth advantage over holding across the longest history at 10 basis points, a positive advantage at 25 basis points, full-period drawdown no worse than 30%, at least 75% of tested two- and five-year starts beating holding, complete discovery-price verification, and positive costed execution advantage in all three diagnostic periods with usable observations. These are explicit **post-screen decision hurdles**, not a claim of preregistration or statistically sharp boundaries. Near-threshold pairs remain visible in the audit.

For example, KR/MEDP loses to holding over the longest history despite its attractive discovery period. MPWR/PGR’s drawdown becomes about 55% once 2008 is included. HII/WMT loses its holding-relative advantage in the full vendor-period execution check. KR/XYL is less consistent across two-year entry dates. These failures are retained alongside the survivors.

Current financial filters and complete-history/liquidity requirements condition the universe on survivorship and availability. The 2026 data had already been inspected during this research. Searching millions of pairs and then selecting winners creates substantial selection bias; overlapping windows do not cure that. The prior conservative family-wide test did not establish statistical significance across the search. I am recommending a controlled trial of specific candidates based on the observed evidence, not claiming a proven forward alpha or a guaranteed return.

**Data verification and cost accounting**

The audit compares vendor nominal official closes with the local series after undoing future splits, resolves documented issuer changes, and prioritizes the primary Cboe listing source where applicable. Every lower-risk qualifier has complete discovery-price coverage; all 354 verification pairs have price-comparison records. Historical special-symbol records that the free source mapped to the wrong ordinary history were quarantined. Price/quote outlier reviews also caught and corrected historical names such as ABC/COR, ABX/GOLD/B, WLTW/WTW, AAXN/AXON and YY/JOYY. Sources and exact date intervals are in {link('IDENTITY_SOURCES.md')}.

Corporate-action factors and most dividend records still come from the local/free source: the Databento reference subscription was not available and was not activated. Independently verified nominal prices plus a shared dividend factor are not a fully independent total-return database. The cash-dividend timing sensitivity and anomaly checks address this explicitly rather than disguising it.

All request quotes total **${charges:.2f}**, within the $120 ceiling; retained reservations total **${reserved:.2f}**, including the buffer and a failed stream replaced with a batch download. The batch-cost reconciliation distinguishes actual vendor batch charges from quoted streaming costs. The account’s remaining credit balance is not inferred from these amounts. See {link('DATA_COST_RECONCILIATION.json')}.

Reproducibility and supporting records: {link('PROTOCOL.md')}, {link('expansion_eligibility.csv')}, {link('all_stock_price_audit.csv')}, {link('pair_price_verification.csv')}, {link('long_period_checks.csv')}, {link('pool_long_rolling.csv')}, {link('execution_results.csv')}, {link('DECISION_FILTER_COUNTS.json')}. All rejected and unavailable identifiers are retained. The core daily fixed-weight design is unchanged; Phase 3 remains deferred.
'''
(P/'REPORT.md').write_text(text);print('WROTE REPORT',len(text.split()),'words',flush=True)
