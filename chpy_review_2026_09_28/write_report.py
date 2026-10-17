from pathlib import Path
import json
import pandas as pd
P=Path(__file__).resolve().parent
r=pd.read_csv(P/'results.csv'); periods=pd.read_csv(P/'periods.csv'); d=json.loads((P/'details.json').read_text())
def table(frame):
    lines=['| Approach | Start | Ending $100,000 | Total return | Maximum drawdown |','|---|---|---:|---:|---:|']
    for x in frame.itertuples():
        name=f'{x.ticker} hold/reinvest' if x.buyhold else f'{x.ticker} capture −{x.before}/+{x.after}'
        lines.append(f'| {name} | {x.start} | ${x.ending_value:,.2f} | {x.total_return:.2%} | {x.max_drawdown:.2%} |')
    return '\n'.join(lines)
text=f'''# CHPY review — September 28, 2026

CHPY holds a portfolio of semiconductor companies and uses options to generate weekly distributions. It spreads company-specific exposure across roughly 15–30 stocks, but remains concentrated in one volatile industry. It is legally non-diversified; a basket is not the same thing as broad-market diversification. Its historical results deserve consideration, but do not establish stable future income or principal protection.

## Current issuer facts

- Inception April 2, 2025; launch announcement April 3, 2025.
- Gross expense ratio 1.03%; embedded in observed fund prices, not charged twice in the backtest.
- September 25 closing price $70.33; issuer NAV $70.20.
- Latest distribution $0.5256 per share, ex September 23, paid September 24.
- At $70.33, a $100,000 position with fractional shares would receive approximately ${d['last_payout_on_100k_at_last_close']:,.2f} if that payout repeated. This is illustrative, before fees/tax, not a promised weekly payment.
- Issuer annualized latest distribution rate 40.95% as of September 22 uses a different date/NAV than the above illustration; it is not total return or a forecast.
- Latest payout estimated 99.74% return of capital. This is preliminary tax classification; it alone does not establish destructive capital erosion or the final taxable character. A payout reduces NAV mechanically, while underlying investment gains may offset it.

Sources: [issuer](https://yieldmaxetfs.com/our-etfs/chpy/), [SEC summary prospectus](https://www.sec.gov/Archives/edgar/data/1924868/000199937125003579/chpy-497k_040125.htm), [issuer launch release](https://www.globenewswire.com/news-release/2025/04/03/3054965/0/en/YieldMax-Launches-Semiconductor-Portfolio-Option-Income-ETF-CHPY.html).

## Comparable recent-period results

All end September 25, 2026. Before tax, $100,000 initial capital, no added capital, 0.05% costs each side. Returns are cumulative, not annualized. Distributions are included once. Buy-and-hold reinvests on payment availability. Capture reinvests available strategy cash on subsequent entries, keeping residual cash idle.

{table(periods[periods.period_start=='2025-09-25'])}

CHPY's one-year holding return exceeded SMH, but trailed SOXX. These are sector benchmarks, not the exact same underlying basket without options. They cannot isolate the causal effect of CHPY's options overlay.

{table(periods[periods.period_start!='2025-09-25'])}

The latest three-month holding period lost money despite cash payouts. The most severe drawdown in the longer holding simulation was 27.55%, June 22–July 29, 2026. This was a decline from the running portfolio peak, not a 27.55% loss from every investor's purchase cost.

## Full available data, and the favorable starting point

Downloaded daily prices begin April 8, 2025, not inception. The prior three trading sessions after launch are missing from this vendor series. They have not been fabricated. April 8 was a particularly favorable entry: CHPY rose 17.46% at the following close. Do not call this an inception backtest or extrapolate its rate of return.

{table(r[(r.buyhold)|((r.before==1)&(r.after==1))|((r.before==5)&(r.after==5))])}

Across the 20 predefined schedules (buy 1/3/5/10 sessions before ex-date; sell 0/1/3/5/10 sessions after), none exceeded holding CHPY in ending wealth over this available period. Best hindsight schedule was −3/+5, ending $288,469.02, versus $307,802.74 holding. Capture usually has less time invested, so lower absolute return alone does not prove no timing benefit. No new multiple-testing significance claim or prospective validation is made here.

### Keeping distributions in cash

With the same April 8, 2025 purchase, $100,000 initially and 0.05% entry cost, no reinvestment and no final sale:

- Share value September 25, 2026: ${d['shares_value_without_reinvestment']:,.2f}.
- Distributions received and retained as cash: ${d['cash_received_without_reinvestment']:,.2f}.
- Outstanding distribution receivable: ${d['receivable_without_reinvestment']:,.2f}.
- Combined wealth: ${d['total_wealth_without_reinvestment']:,.2f}.
- Share price rose {d['price_return']:.2%}, from ${d['first_close']:.4f} to ${d['last_close']:.2f}, alongside $36.9013 per share in cash payouts. These amounts are not a forecast for a purchase today.

This observed period shows appreciation alongside distributions, not persistent depletion of the initial share value. If distributions were spent, only the share value would remain invested; the spent money would not still be portfolio wealth.

## Implementation and audit

- Reuses the previous research engine without changing its source files or original results. `research.py` records the exact adapter.
- Audited 76 issuer payouts; replaced rounded vendor amounts and used actual issuer payment dates. All vendor payout dates were represented in issuer records; no splits were observed.
- Uses unadjusted-for-dividend closes plus cash, not adjusted-close returns plus dividends. Prices already reflect any provider split adjustments; none occurred in this sample.
- T+1 sale settlement; fractional shares; no borrowing; zero idle-cash yield. New entries cannot use unsettled sale proceeds.
- One concurrent position. Overlapping weekly capture signals are skipped, not stacked. A longer window may collect multiple distributions. The alternating selection of weekly cycles makes results dependent on starting date.
- Observed ex-dates with exits beyond cutoff are included, marking the open position at the last close without a fictional sale fee. Future ex-dates after the cutoff are not fabricated. Closed-trade count excludes open positions; ledger terminal marks are identifiable at the cutoff. The inherited win-rate field includes marked positions and is not used for conclusions.
- Capture cash flows reconcile terminal wealth within $0.00001; nonnegative settled cash and one-position limits are asserted.
- Trading calendar known at entry is assumed; historical calendar publication timestamps were not reconstructed.
- Baseline 5 bps each side may understate execution costs. The issuer's current median bid/ask spread is 0.15%. Early trading was thin: first 30 sessions' median closing-price-times-volume was about $330,774/day, making $100,000 execution material. No historical depth/market-impact model was available.
- At 20 bps per side, full available-period −1/+1 ends $105,527.47 (versus $132,432.24 baseline); −5/+5 ends $197,783.07 (versus $211,816.81 baseline). See `cost_sensitivity.csv`.
- SMH/SOXX benchmark payout dates use a modeled five-calendar-day delay; CHPY uses actual issuer dates. Benchmark prices/distributions are vendor data and not independently audited payout by payout.
- Under 18 months of available prices, selected retrospectively after attention to recent performance. No prolonged semiconductor bear market test is possible using CHPY's actual history.

Research judgment: CHPY is more suitable for further diversified-sector income research than a single-company comparison suggests, but it remains an aggressive semiconductor allocation. Evidence here supports strong past total returns and meaningful income, with substantial drawdowns and no demonstrated full-period wealth advantage from capture. It does not establish a safe 40% return or a dependable replacement for lost capital. No trade or automation was implemented.

Files: `results.csv` (all 20 schedules), `periods.csv` (later starts), `cost_sensitivity.csv`, `details.json`, issuer audit and source HTML, daily equity curves, and transaction ledgers.
'''
(P/'REPORT.md').write_text(text)
print(P/'REPORT.md')
