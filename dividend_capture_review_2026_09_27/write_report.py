from pathlib import Path
import pandas as pd,json
P=Path(__file__).resolve().parent
R=pd.read_csv(P/'rotation_summary.csv');S=pd.read_csv(P/'stock_summary.csv');E=pd.read_csv(P/'event_summary.csv');A=pd.read_csv(P/'annual_returns.csv');Q=pd.read_csv(P/'periods.csv');X=pd.read_csv(P/'rotation_sensitivity.csv');F=pd.read_csv(P/'portfolio_summary.csv');G=pd.read_csv(P/'events.csv')
pct=lambda x:f'{x*100:.2f}%'
money=lambda x:f'${x:,.0f}'
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
base=R[(R.before==5)&(R.after==5)].iloc[0];bh=F[F.buyhold].iloc[0];primary=E[(E.high_yield)&(E.before==5)&(E.after==5)].iloc[0]
text=f'''# Dividend capture backtest — September 27, 2026

**Finding:** dividend capture produced mixed, generally modest results in this sample. Buying five sessions before the ex-date and selling five sessions after grew a rotating $100,000 account to **{money(base.ending_value)}**, or **{pct(base.CAGR)} a year**, with a **{pct(base.max_drawdown)} maximum drawdown**. The shortest version lost money. The study does not establish a repeatable advantage from dividend timing over ordinary stock exposure.

This is an exploratory, reproducible historical model, not a forward forecast or a claim of broker-reconciled execution. No trading or trading automation was implemented.

![Portfolio paths](performance.png)

## Universe and dates

January 2, 2015–September 25, 2026; 2,950 common daily sessions. Downloads begin in 2014 for historical yield calculations. Exact session count and checks appear in `diagnostics.json`.

Twelve established US dividend payers, chosen before inspecting their capture returns: Realty Income (O), Verizon (VZ), Philip Morris (PM), Altria (MO), Exxon Mobil (XOM), Chevron (CVX), PepsiCo (PEP), Coca-Cola (KO), Johnson & Johnson (JNJ), Procter & Gamble (PG), Southern Company (SO), Duke Energy (DUK).

These are a case-study sample of established businesses with dividend histories, **not a verified investment-grade or point-in-time financial-quality screen**. Tobacco, energy, telecom and REIT exposures carry distinct risks. Current survivors were selected; failed companies and historical dividend cutters were not systematically included. Results cannot be generalized to every high-yield stock.

For the main results, an event qualifies only when the stock's cash dividends in the preceding 365 calendar days divided by the entry close were at least 3%. This uses previous dividend amounts, not a future annual payout. The all-yield version is retained separately. JNJ, PEP and PG consequently have small qualifying samples.

## Rules tested

- Entry at the close either one or five trading sessions before the ex-dividend date.
- Exit at the close on the ex-date, five sessions afterward, or ten sessions afterward. These are trading sessions, not calendar days or payment dates.
- All six combinations were specified before observing returns. Five-before/five-after is the illustrative primary rule; the highest-return row is not a validated optimum.
- Cash dividends plus sale proceeds less entry price and costs determine profit. Dividend-adjusted prices are never combined with an extra dividend credit.
- Cost: 0.05% of trade value on purchase and on sale. Includes an assumed spread/slippage allowance, not a broker commission claim. Zero and 0.10% per-side alternatives are shown.
- No leverage, no fresh capital, fractional shares, idle cash earns zero. Taxes excluded.

Historical ex-dates are assumed known at entry. The provider does not supply a complete history of declaration timestamps, so this is **not a fully point-in-time executable calendar backtest**. Price/yield ranking uses the entry close with execution at that close, another idealization requiring a live order-timing rule.

## Rotation of a $100,000 account

At most four concurrent positions, each new position limited to 25% of account equity and available cash. No duplicate ticker. When entry dates compete, higher trailing dividend yield takes priority, with ticker as tie-breaker. Positions may be smaller if cash is unavailable. Sale proceeds settle after T+2 trading sessions before May 28, 2024, and T+1 afterward. Cash distributions become receivables on ex-date and spendable after an assumed 30 calendar days; complete historical payment dates were unavailable. A 45-day sensitivity is included. All equity values include unpaid receivables.

The rotation extension was added **after observing the separate-sleeve tests**, because those left most money idle. Its rules were not optimized, but it is still exploratory rather than a fresh holdout. None of these results tests a maximally invested, unconstrained rotation universe.

'''
text+=table(['Buy before ex-date','Sell after ex-date','Ending $100k','Annualized return','Worst drawdown','Average stock exposure','Closed trades'],[[f'{int(r.before)} sessions','Ex-date close' if r.after==0 else f'{int(r.after)} sessions',money(r.ending_value),pct(r.CAGR),pct(r.max_drawdown),pct(r.avg_invested),int(r.trades)] for r in R.itertuples()])
text+=f'''

Buy-and-hold of the same 12 names, initially equally weighted, with dividends reinvested after the same payment lag, ended at **{money(bh.ending_value)}**, **{pct(bh.CAGR)} annualized**, with a **{pct(bh.max_drawdown)} drawdown**. It was invested **{pct(bh.avg_invested)}** on average versus **{pct(base.avg_invested)}** for the primary capture model. This is useful wealth/risk context, not an equal-exposure proof that capture has no timing advantage. Buy-and-hold ending value is marked to market, without a terminal liquidation charge.

## Cash received versus actual profit

For five-before/five-after rotation:

- Dividends accrued: **{money(base.dividends_accrued)}** over the entire 11.73-year study, not per year.
- Net account gain: **{money(base.ending_value-100000)}**.
- Share-price changes plus transaction costs: **{money(base.ending_value-100000-base.dividends_accrued)}**.
- Explicit simulated trading costs: **{money(base.costs)}**; these are already included in the previous line and must not be subtracted twice.
- Winning executed trades: **{pct(base.win_rate)}** across **{int(base.trades)}** trades.

Dividends are reinvested into later trades when cash becomes available. They are not withdrawn as spendable income in these terminal-wealth figures. The cumulative dividend total is not additional wealth on top of the ending account value.

## Individual capture events

The following includes every qualifying event before account-capacity constraints; it is not the 501-trade rotation ledger. Primary five-before/five-after window, net of 0.05% costs per side:

'''
text+=table(['Stock','Events','Mean net return per trade','Win rate','Worst trade'],[[r.ticker,int(r.events),pct(r.mean),pct(r.win_rate),pct(r.worst)] for r in S[S.high_yield].sort_values('ticker').itertuples()])
text+=f'''

Across **{int(primary.events)}** qualifying events, the mean net return was **{pct(primary['mean'])}**, median **{pct(primary['median'])}**, and win rate **{pct(primary.win_rate)}**. Mean dividend collected was **{pct(primary.mean_dividend)}** of purchase price; mean price change was **{pct(primary.mean_price)}**. Average winning trade: **{pct(primary.mean_win)}**; average losing trade: **{pct(primary.mean_loss)}**.

The worst event was Altria, June 7–June 22, 2022: **−21.57% net including the dividend**. Thus a high dividend did not bound the possible stock loss. Other large losses appear in `diagnostics.json` and `events.csv`.

A 10,000-resample calendar-year block bootstrap gives a 95% interval of approximately **{primary.ci_low*100:.4f}% to {primary.ci_high*100:.4f}%** for the primary mean event return. Resampling whole years preserves same-year cross-stock dependence better than treating each trade as independent, but only 12 year blocks exist and 2026 is partial. The near-zero lower endpoint, multiple tested windows, selected survivor universe, and lack of prospective validation prevent a strong statistical claim.

## Did the dividend timing add something?

For each event, the comparison averages non-dividend holding windows of the same length in the same stock and entry year, also requiring a 3% trailing yield and identical trading costs. These control windows overlap and are a descriptive reference, not a randomized or fully seasonality-matched experiment.

Primary capture events averaged **{pct(primary['mean'])}** versus **{pct(primary.control_mean)}** for these ordinary windows. The capture-minus-control difference was **{primary.excess*100:.3f} percentage points**, with a year-block 95% interval of **{primary.excess_ci_low*100:.3f} to {primary.excess_ci_high*100:.3f} percentage points**. This interval includes zero: **the test did not establish a positive dividend-timing advantage**.

'''
text+=table(['Entry/exit sessions','Events','Mean net/event','Win rate','Mean ordinary-window return'],[[f'-{int(r.before)} / +{int(r.after)}',int(r.events),pct(r.mean),pct(r.win_rate),pct(r.control_mean)] for r in E[E.high_yield].itertuples()])
text+='\n\n## Yearly account returns\n\n'
pivot=A[A.strategy.isin(['rotation_b5_a5','rotation_b5_a10','buyhold'])].pivot(index='year',columns='strategy',values='return_total')
text+=table(['Year','Capture −5/+5','Capture −5/+10','Buy-and-hold'],[[str(y)+(' YTD' if y==2026 else ''),pct(r.rotation_b5_a5),pct(r.rotation_b5_a10),pct(r.buyhold)] for y,r in pivot.iterrows()])
text+='\n\n## Sensitivities and recent period\n\n'
text+=table(['Five-before/five-after setting','Ending $100k','Annualized return','Worst drawdown'],[[('Exclude O' if r.no_o else '45-day dividend delay' if r.payment_lag==45 else f'{r.cost_bps:.0f} bps cost each side'),money(r.ending_value),pct(r.CAGR),pct(r.max_drawdown)] for r in X.itertuples()])
recent=Q[(Q.strategy=='rotation_b5_a5')&(Q.period=='2023-2026')].iloc[0]
text+=f'''

During 2023–September 2026, the primary rotation model returned **{pct(recent.total_return)} cumulatively**, about **{pct(recent.annualized_return)} annualized** (subperiod annualization uses 252 sessions/year). This was much weaker than its earlier periods. Subperiod returns continue the existing portfolio, rather than resetting holdings at each boundary.

Excluding O reduced primary rotation CAGR to about 1.06%. The result is sensitive to the monthly payer's inclusion; quarterly names alone were less compelling. No names were excluded from headline results for poor performance.

## Data and validation limits

Yahoo Finance daily prices/actions were retrieved September 27, 2026 with yfinance 1.2.0. Original CSVs and SHA-256 hashes are retained in `data/` and `manifest.json`. This is one market-data provider, not independently validated institutional data.

Issuer spot checks found Yahoo rounds some cash dividends to three decimals: for example Verizon's $0.6775 appears as $0.678, and O's April 2025 $0.2685 appears as $0.269. Original provider values were retained consistently, and discrepancies saved in `issuer_spot_checks.csv`. Assuming every sampled dividend is overstated by $0.0005/share changes the primary mean event return by about 0.00075 percentage points with the event set held fixed; near-threshold eligibility may also change. Reported precision describes this model, not cent-perfect historical broker accounting. [Verizon dividend history](https://www.verizon.com/about/investors/dividend-history); [O 2025 issuer dividend table](https://www.realtyincome.com/sites/realty-income/files/realty-income/shareholder/2025-Common-Stock-Dividend-Allocation.pdf).

O's Orion spin-off is encoded by Yahoo as a synthetic 1.032 split on November 15, 2021. The model retains that provider-adjusted price/dividend basis; it does not separately reconstruct ownership and liquidation of Orion stock. The no-O sensitivity removes this approximation entirely. A direct ONL history request failed and the failed request is not treated as verified price data. [Issuer spin-off announcement](https://www.realtyincome.com/investors/press-releases/realty-income-completes-spin-orion-office-reit).

All 21 saved capture portfolio ledgers reconcile their terminal equity to initial capital plus independently summed sale proceeds, dividends and entry outlays, with maximum residual below $0.000001. Checks verify positive prices, common sessions, no duplicate dates, no leverage, no duplicate ticker positions in rotation, no missing primary prices, no spendable dividend cash before the assumed payment lag, and no dividends counted twice. Adjusted-close differences were audited but not used as an extra return source.

The backtest excludes taxes. Short-term captures generally fail the holding-period requirement for qualified common-stock dividends; REIT distributions have different tax character, and repeated loss/replacement trades may create wash-sale deferrals. We did not apply one simplistic tax rate to all payments. [IRS Publication 550](https://www.irs.gov/publications/p550).

**Assessment:** This sample supports a modest and inconsistent trading result, not a strong dividend-capture edge. The highest-return tested window earned approximately 3.19% annually while retaining stock-loss risk. A larger point-in-time universe, verified declaration/payment dates, accurate corporate-action processing, and unseen-period validation would be needed before treating it as a deployable income strategy.

## Reproduction and files

`download.py` fetches prices/actions; `analyze.py` creates event/separate-sleeve tests; `rotation.py` models capital reuse; `audit.py` verifies the raw hashes and cash-flow ledgers and refreshes annual/subperiod summaries; `write_report.py` creates this report; `plot.py` renders the figure with Matplotlib. Preserve the raw CSVs for exact reproduction instead of redownloading revised historical data.

Core outputs: `events.csv`, `event_summary.csv`, `stock_summary.csv`, `rotation_summary.csv`, `rotation_sensitivity.csv`, `portfolio_summary.csv`, `annual_returns.csv`, `periods.csv`, `trades_rotation_b5_a5.csv`, `daily_rotation_b5_a5.csv`, `cashflow_reconciliation.csv`, `diagnostics.json` and `PROTOCOL.md`.
'''
# Actual count rather than a handwritten approximate count.
diag=json.loads((P/'diagnostics.json').read_text());text=text.replace('2,950 common daily sessions',f"{diag['sessions']:,} common daily sessions")
(P/'REPORT.md').write_text(text)
print('Report written:',len(text.split()),'words. Sessions:',diag['sessions'])
