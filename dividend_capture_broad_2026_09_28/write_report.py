from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
S=pd.read_csv(P/'security_portfolios.csv');R=pd.read_csv(P/'rotation_portfolios.csv');V=pd.read_csv(P/'frozen2025_validation.csv');W=pd.read_csv(P/'walkforward_portfolios.csv');T=pd.read_csv(P/'timing_placebo.csv');C=pd.read_csv(P/'category_event_summary.csv');ES=pd.read_csv(P/'security_event_summary.csv');A=pd.read_csv(P/'annual_returns.csv');X=pd.read_csv(P/'sensitivity.csv');B=pd.read_csv(P/'robustness.csv');REC=pd.read_csv(P/'reconciliation.csv');U=json.loads((P/'universe.json').read_text());EVENTS=pd.read_csv(P/'events.csv');AUD=pd.read_csv(P/'issuer_audit.csv')
OT=pd.read_csv(P/'open_tail_sensitivity.csv');OR=pd.read_csv(P/'open_tail_rotation.csv')
pct=lambda x:f'{x*100:.2f}%';money=lambda x:f'${x:,.0f}'
def tab(h,rs):return '\n'.join(['| '+' | '.join(h)+' |','| '+' | '.join(['---']*len(h))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rs])
def sec(s,period='all',b=5,a=5,bh=False):
 if period=='common2025' and not bh and (b,a)==(5,5):
  r=OT[OT.ticker==s].iloc[0].copy();r['trades']=r.trades-r.open_at_cutoff;return r
 return S[(S.ticker==s)&(S.period==period)&(S.before==b)&(S.after==a)&(S.buyhold==bh)].iloc[0]
q=sec('QQQI');qh=sec('QQQI',bh=True);qbest=S[(S.ticker=='QQQI')&(S.period=='all')&(~S.buyhold)].sort_values('CAGR',ascending=False).iloc[0]
labels={'option_income':'Option-income ETFs','higher_risk_option_income':'Higher-risk option funds','us_dividend_etf':'US dividend ETFs','global_dividend_etf':'Global dividend ETFs','reit':'REITs / REIT ETF','bdc':'BDCs / BDC ETF','preferred_etf':'Preferred-stock ETFs','closed_end_fund':'Closed-end funds','dividend_stock':'Dividend stocks','core_income':'Core income rotation','broad':'All 92 rotation'}
text=f'''# Broader dividend-capture research — September 28, 2026

**Result: mixed, with meaningful exceptions.** The earlier 12-stock result was too narrow to support a general conclusion. This extension covers **92 distribution-paying securities**, nine categories, **{len(EVENTS[['ticker','k']].drop_duplicates()):,} distinct distribution dates**, and **20 entry/exit schedules**. QQQI is included explicitly. Some captures were profitable, and TSLY and EPR beat holding those same securities over their tested histories. A simple rotation through the highest recent distribution yields did not reliably turn those exceptions into a strong broad portfolio. No trades or trading automation were implemented.

The 138,000+ event-window observations share many of the same price paths; they are **not independent experiments**. Counts below document coverage, not statistical certainty. All values are model results under the stated assumptions, before taxes.

## QQQI first

QQQI has **32 observed monthly distributions**, not a decade of history. Its issuer gives inception as January 29, 2024; our first daily price is January 30, 2024. The comparison below uses **January 30, 2024–September 25, 2026** for both capture and holding. [QQQI issuer page](https://neosfunds.com/qqqi/).

All rows begin with $100,000, use fractional shares, no leverage or top-ups, 0.05% trading cost per purchase/sale and zero return on cash. Actual issuer payment dates are used. Capture invests available cash into QQQI and exits according to the stated rule; it does not maintain a continuous fully invested position.

'''
text+=tab(['QQQI rule (trading sessions)','Ending $100k','Annualized return','Worst drawdown','Completed trades'],[[f'Buy {b} before / sell {a} after',money(sec('QQQI',b=b,a=a).ending_value),pct(sec('QQQI',b=b,a=a).CAGR),pct(sec('QQQI',b=b,a=a).max_drawdown),int(sec('QQQI',b=b,a=a).trades)] for b,a in [(1,0),(1,1),(3,3),(5,5),(10,3)]]+[['Buy and hold; reinvest payable distributions',money(qh.ending_value),pct(qh.CAGR),pct(qh.max_drawdown),'—']])
text+=f'''

The −10/+3 row is the **best in hindsight** among the tested QQQI schedules; it is not a validated forecast or a selected live recommendation. QQQI −5/+5 held market exposure averaging **{pct(q.exposure)}**, versus **{pct(qh.exposure)}** for holding. Thus the return gap also reflects time in the market. Capture accrued **{money(q.distributions)}** in distributions but earned **{money(q.ending_value-100000)}** in total account profit. Distributions are already included in ending wealth, not additional income to add on top.

![QQQI results](qqqi_results.png)

## Matching-period comparisons

For a matching date range across all 92 securities, use **January 2, 2025–September 25, 2026**. Under the primary −5/+5 schedule, **54 of 92** securities made money and **14 of 92** beat holding the same security on total wealth. These counts include marked open positions at cutoff and do not adjust for differing market exposure. The primary schedule below buys five sessions before ex-date and sells five sessions after. These are cumulative account returns, not annual yields. Each security is a separate $100,000 experiment. Rows were chosen to show major income products and positive/negative exceptions, not as a recommended portfolio. All 92 rows appear in the appendix and `comparison_common2025.csv`.

'''
chosen=['QQQI','SPYI','JEPI','JEPQ','DIVO','QYLD','XYLD','RYLD','SCHD','QDTE','XDTE','TSLY','NVDY','CONY','STAG','EPR','MAIN','ARCC','PFF']
text+=tab(['Security','Capture return','Buy-and-hold return','Capture drawdown','Capture trades'],[[s,pct(sec(s,'common2025').total_return),pct(sec(s,'common2025',bh=True).total_return),pct(sec(s,'common2025').max_drawdown),int(sec(s,'common2025').trades)] for s in chosen])
text+='''

The common-2025 individual and category tables include positions still open after the last observed ex-date, marked at the September 25 close without a fictional sale fee. Trade counts in the individual table count completed trades; open positions remain in ending wealth. The original 20-window and temporal-validation files use completed scheduled windows, and remain available for comparison. The as-of extension does not fabricate future unobserved distribution dates beyond the price-data cutoff. Weekly-paying funds can have multiple distributions inside a holding window. The event table records those overlaps, but portfolio paths never pretend the same dollar can fund simultaneous overlapping positions in the same fund. Funding/settlement can force trades to be skipped or only partially funded.

## Longer-history positive exceptions

Do not compare these annualized figures against one another without considering their differing start dates. Each capture-versus-hold comparison uses matching dates within the row.

'''
text+=tab(['Security','First tested date','Capture CAGR','Holding CAGR','Capture drawdown','Holding drawdown'],[[s,sec(s).start,pct(sec(s).CAGR),pct(sec(s,bh=True).CAGR),pct(sec(s).max_drawdown),pct(sec(s,bh=True).max_drawdown)] for s in ['TSLY','EPR','STAG','DIVO','JEPQ','QDTE']])
text+='''

TSLY is a volatile Tesla-linked option fund, not interchangeable with a conventional dividend-quality stock. EPR experienced dividend suspension during the pandemic, and its buy-and-hold drawdown was extreme. These are useful historical exceptions, not evidence that selecting them today would repeat the outcome. STAG's capture result was close to holding with less historical drawdown. DIVO and JEPQ captures made money but trailed holding their respective funds.

## Rotating a single account across securities

These are **real shared-capital simulations**, not averages of independent trade returns. Start with $100,000; maximum four concurrent tickers; each new position capped at 25% of equity and available cash. Competing entries prioritize previous-session trailing distribution yield (past 126 sessions, normalized for available history), then ticker. At least $1m prior average daily dollar volume is required. There is no use of the next declared payout amount to rank trades. New orders below 1% of account equity are skipped. Proceeds cannot be spent before modeled stock-sale settlement.

The following uses the same January 2025–September 2026 period and −5/+5 schedule. The reference holds the same constituent basket with equal initial allocations and reinvests payable distributions equally, without selling/rebalancing existing shares.

'''
rt=R[(R.period=='common2025')&(R.before==5)&(R.after==5)].copy()
for ii,rr in rt[~rt.buyhold].iterrows():
 oo=OR[OR.pool==rr.pool].iloc[0]
 for col in oo.index.intersection(rt.columns):rt.loc[ii,col]=oo[col]
text+=tab(['Rotation universe','Capture ending $100k','Capture cumulative return','Capture drawdown','Average stock exposure','Holding ending $100k'],[[labels[c],money(rt[(rt.pool==c)&(~rt.buyhold)].iloc[0].ending_value),pct(rt[(rt.pool==c)&(~rt.buyhold)].iloc[0].total_return),pct(rt[(rt.pool==c)&(~rt.buyhold)].iloc[0].max_drawdown),pct(rt[(rt.pool==c)&(~rt.buyhold)].iloc[0].exposure),money(rt[(rt.pool==c)&rt.buyhold].iloc[0].ending_value)] for c in labels])
text+='''

“Core income” combines option-income ETFs, US dividend ETFs, REITs, BDCs and preferred funds; it is a research label, not a low-risk or investment-grade certification. The all-92 rotation includes the explicitly higher-risk group. Selecting high recent payouts can concentrate the account in distressed or unusually volatile products.

An additional benchmark applies each strategy's previous-day stock-exposure fraction to the same basket's daily total return, with zero return on cash. It is a mechanical risk-context reference, not an executable strategy: it omits rebalancing costs and does not precisely match security selection or factor exposure. In the completed-window diagnostic, the option-income rotation ended near $112,085 versus about $115,748 for this exposure-matched reference. The all-92 rotation ended near $87,892 versus about $121,177. Those diagnostic values use closed-window paths; the headline common-period tables add open positions at cutoff. Lower exposure alone therefore does not explain all the weakness in those two cases.

![Category comparison](category_results.png)

## Twenty windows, costs and execution checks

The entire timing grid was fixed before inspecting expanded returns: buy 1, 3, 5 or 10 trading sessions before ex-date; sell on ex-date or 1, 3, 5 or 10 sessions after. Primary anchors were −1/+1 and −5/+5. No stop-loss, price-recovery target or unconstrained timing optimizer was introduced.

All 20 windows were simulated for each security over its available history. Common-date individual comparisons include the two primary anchors. Core-income, all-92 and option-income rotations also have all 20 windows on both common periods. Other category rotations use the two anchors. The best observed row in each grid is an exploratory result; neighboring windows and later-period outcomes matter more than a lone maximum.

Costs of 0, 2, 5 and 10 basis points each side were checked for selected major securities and rotations. Full per-event cost summaries are also provided. Expenses charged inside ETFs are already reflected in prices; no second fund-fee deduction was made.

QQQI −5/+5 returned **6.12% annually at zero trading costs, 5.61% at 2 bps per side, 4.86% at 5 bps, and 3.61% at 10 bps**. Buying at the open of the same scheduled entry day instead of its close reduced the result to **2.29% annually**. That sensitivity is a reason not to present 4.86% as a stable forward return.

A 95%-of-target funding requirement tests whether tiny partial positions create misleading wins. QQQI was unchanged. TSLY −5/+5 fell from about 19.20% to 18.27% annually over full history; its result was still positive. Buying at the scheduled day's open produced about 18.03% annually for TSLY, 7.33% for EPR and 7.03% for STAG. Exact values are in `robustness.csv`. Other checks delay dividend availability by another 30 calendar days, apply a historical 3% trailing-yield requirement after one year of history, and exclude selected spin-off-adjusted names from rotation.

## Later-period and timing validation

**Frozen selection:** for each eligible security, choose the highest mean-return timing window using only events fully completed before 2025, requiring at least 12 training events. Freeze that window for January 2025 onward. This produced 88 eligible securities. QQQI had only 11 pre-2025 distributions, so it correctly failed the minimum-history criterion; it still receives all fixed-rule/common-period tests. The fixed universe itself is retrospectively chosen, so this is temporal validation, not a completely untouched investment experiment.

The later test contains positive exceptions. NVDY's pre-2025-selected −10/+10 window produced a substantial gain in 2025–2026 while the predefined −5/+5 rule lost heavily. That demonstrates **parameter dependence**, not permission to select whichever window won after observing the later period. TSLY's pre-2025-selected window was −10/+5, different from the illustrative −5/+5 anchor. See `frozen2025_validation.csv` for all 88 selections, not just winners.

**Rolling yearly selection:** select the next year's category timing rule from the preceding year's completed events, with at least 50 training observations per candidate. Then simulate its shared-capital rotation during the next year, adding funds only after they actually exist. Years without sufficient training remain uninvested. Results:

'''
text+=tab(['Category','First test year','Ending $100k','Annualized return','Worst drawdown','Trades'],[[labels[r.category],r.start,money(r.ending_value),pct(r.CAGR),pct(r.max_drawdown),int(r.trades)] for r in W.itertuples()])
text+='''

**Timing placebo:** for each of 92 securities and both primary windows on the 2025–2026 period, circularly shift its daily stock-exposure fractions within each calendar year 2,000 times. This preserves time/capital exposure while changing its relation to dividend dates. Observed and placebo paths use the same daily total-return convention and entry/exit-cost approximation. It is an exposure-timing diagnostic, not a fully executable randomized cash-settlement model. No result cleared a 5% Benjamini–Hochberg false-discovery threshold across the 184 comparisons. This is failure to establish an edge with this test, **not proof that every capture strategy has zero edge**.

There is also a same-security, same-year, same-duration non-dividend-window comparison, with quarter-block bootstrap intervals. Those control windows overlap; very frequent payers can lack eligible non-dividend windows. They are descriptive controls, not randomized experiments. All window tests are correlated, and new funds have short samples.

## Data integrity and remaining limits

- **95 downloaded price histories, zero silent failures:** 92 research securities plus SPY, QQQ and IWM context series. Original files, dates, hashes and download errors are retained; prices end September 25, 2026. History before a fund's launch is never invented. The earliest study start is January 2015.
- **194 issuer payout records** for QQQI, SPYI, IWMI and TSLY replace rounded vendor amounts and supply actual payment/declaration dates. Issuer HTML and normalized rows are saved separately.
- QQQI/SPYI's current website transposes ex and record dates in nine early-2024 rows. Contemporaneous issuer notices confirm the record dates; using the pre-T+1 ex-date relation reproduces the vendor's ex dates. The model does not count both adjacent dates. [QQQI February 2024 notice](https://neosfunds.com/wp-content/uploads/QQQI-19a1-Notice-2.21.24-Confidential.pdf); [May notice](https://neosfunds.com/wp-content/uploads/QQQI-19a1-Notice-5.22.24-Confidential.pdf).
- Yahoo omits SPYI's January 25, 2023 payout and TSLY's September 24, 2026 payout. Both are added from issuer records. [SPYI contemporaneous notice](https://neosfunds.com/wp-content/uploads/SPYI-19a1-Notice-1.24-in-PDF-Confidential.pdf); [TSLY issuer history](https://yieldmaxetfs.com/our-etfs/tsly/).
- TSLY underwent reverse splits. Issuer older rows already reflect one earlier split; normalization to the current price-series share basis is saved in `issuer/TSLY_normalization_audit.csv`. Never compare a pre-split dividend with a post-split share price. Other symbols retain provider split-adjusted price/distribution units. Synthetic spin-off adjustments and closed-end-fund corporate actions are not fully reconstructed security by security.
- Outside those four issuer-verified names, distribution amounts can be rounded and declaration/payment histories are not fully validated. Funds use a modeled 5-calendar-day payment lag, stocks/REITs/BDCs 30 days; the sensitivity adds 30 days. Some category constituents are ETFs but deliberately inherit conservative category lags. These are limitations, not exact broker statements.
- A calendar-based capture strategy can use published schedules before the payout amount is declared. This research assumes historical ex-date schedules were available at entry; it does not have contemporaneous calendar snapshots for all names. The strict declaration-before-entry-date sensitivity is especially restrictive for NEOS funds, which often declare the day before ex-date. It tests eligibility under that stricter rule, not the availability of advance distribution calendars.
- Raw closing prices plus cash distributions are used once. Adjusted closing prices are never combined with another payout credit. Return of capital is included as cash but not automatically called earnings or treated as destructive. [NEOS distribution discussion](https://neosfunds.com/qqqi/); [Global X distribution definitions](https://www.globalxetfs.com/funds/qyld/).
- Stock-sale cash settlement is T+2 before May 28, 2024 and T+1 afterward. Dividend receivables affect wealth when earned but are unavailable to buy until payable. No leverage, negative cash, duplicate concurrent ticker position or fresh capital. Every saved closed-window capture path reconciles independently to initial capital plus its trade cash flows. The open-position extension additionally includes marked unrealized profit/loss in that identity.
- All results are **pre-tax**. Qualified dividends, REIT income, option-fund distributions, return of capital, foreign withholding, short-term gains and wash sales cannot honestly be reduced to one tax rate. No after-tax superiority is claimed.
- This remains a **selected-survivor study**. Dividend-cut examples are included, but delisted/liquidated securities are not a comprehensive point-in-time universe. It does not cover every preferred issue, every ETF, foreign exchange, leveraged distribution product or closed fund. “Broad” describes this documented 92-name universe, not all dividends everywhere.

## Decision supported by the numbers

QQQI deserved inclusion and can produce positive capture returns, but the tested fixed rules did not outperform holding it on total wealth. TSLY and EPR are positive historical exceptions; STAG offers an interesting near-holding-return / lower-drawdown pattern. Those findings justify selective investigation, not a universal rejection of dividend capture. At the same time, highest-payout rotation across a broad menu was weak, outcomes were highly timing-dependent, and the timing diagnostics did not establish a repeatable advantage after allowing for multiple comparisons.

## Full 92-security comparison, common 2025–2026 period

The same −5/+5 rule and start/end dates apply throughout. Capture ending values include cash and unpaid distributions. Negative returns are retained. The universe table also makes explicit why this is not a collection of uniformly high-quality investments.

'''
comp=[]
for cat,ss in U.items():
 if cat=='benchmark':continue
 for s in ss:
  r=sec(s,'common2025');h=sec(s,'common2025',bh=True)
  comp.append(dict(ticker=s,category=cat,capture_return=r.total_return,hold_return=h.total_return,capture_CAGR=r.CAGR,hold_CAGR=h.CAGR,capture_drawdown=r.max_drawdown,hold_drawdown=h.max_drawdown,capture_end=r.ending_value,hold_end=h.ending_value,trades=int(r.trades),open_positions=int(r.open_at_cutoff),exposure=r.exposure))
pd.DataFrame(comp).to_csv(P/'comparison_common2025.csv',index=False)
text+=tab(['Ticker','Category','Capture return','Holding return','Capture drawdown','Closed trades','Open positions'],[[r['ticker'],labels[r['category']],pct(r['capture_return']),pct(r['hold_return']),pct(r['capture_drawdown']),r['trades'],r['open_positions']] for r in comp])
text+='''

## Reproducibility

Run `download.py`, `issuer.py`, `core.py`, `events.py`, `portfolio.py`, `validation.py`, `robustness.py`, `boundary_check.py`, then `write_report.py` in that order. Preserve raw data for exact reproduction; a fresh download may revise history. `plot.py` requires Matplotlib and renders the two figures. Python, pandas, numpy, yfinance and curl_cffi are used for calculations/retrieval. The analysis requires no brokerage connection.

Start with `comparison_common2025.csv` for the complete same-date comparison, `security_portfolios.csv` for all individual timing paths, `rotation_portfolios.csv` for shared-capital results, `sensitivity.csv` and `robustness.csv` for assumptions, `frozen2025_validation.csv` / `walkforward_portfolios.csv` for temporal validation, and `timing_placebo.csv` for exposure-timing diagnostics. Every saved path has daily values in `daily/` and capture transactions in `ledgers/`. Protocol changes and data corrections are documented in `PROTOCOL.md`.
'''
text+=f'\nAudit count: {len(REC)} saved capture ledgers; maximum terminal cash-flow reconciliation error {REC.cashflow_error.abs().max():.3g} dollars.\n'
(P/'REPORT.md').write_text(text)
# Cost grid at event level for all securities/windows; retain separate from funded portfolios.
costs=[]
for cost in [0,.0002,.0005,.001]:
 z=EVENTS[['ticker','category','before','after']].copy();z['net']=(EVENTS.exit_price*(1-cost)+EVENTS.distributions)/(EVENTS.entry_price*(1+cost))-1;z['win']=z.net>0
 z=z.groupby(['ticker','category','before','after']).agg(events=('net','size'),mean=('net','mean'),win_rate=('win','mean')).reset_index();z['cost_bps']=cost*10000;costs.append(z)
pd.concat(costs).to_csv(P/'event_cost_grid.csv',index=False)
print('Report',len(text.split()),'words; full comparison',len(comp),'securities.')
