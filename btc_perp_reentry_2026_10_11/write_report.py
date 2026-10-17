from pathlib import Path
import json,hashlib
import pandas as pd
P=Path(__file__).resolve().parent
x=pd.read_csv(P/'grid_results.csv');s=pd.read_csv(P/'leader_stress.csv');f=json.loads((P/'frozen_leaders.json').read_text());leaders=f['family_ids'];a=json.loads((P/'data_audit.json').read_text());v=json.loads((P/'validation.json').read_text())
def table(z):return z.to_string(index=False,float_format=lambda n:f'{n:.6f}')
counts=x.groupby('period').agg(cells=('id','size'),positive_net=('net',lambda z:(z>0).sum()),best_net=('net','max'),median_net=('net','median')).reset_index()
lead=x[x.id.isin(leaders)][['id','entry','tp_bps','sl_bps','reentry','period','trades','gross','funding','fees','slippage','net','dd']]
naive=x.query('entry=="long" and tp_bps==25 and sl_bps==25 and reentry=="immediate"')
text=f'''BTC PERPETUAL ENTRY / TARGET / STOP / RE-ENTRY STUDY
Completed October 11, 2026. Research only. Existing trading bots and stable checkpoint unchanged.

RESULT
No tested design established an actual net edge. All 480 configurations lost money in the development period under the base cost/execution assumptions. None of the six development-selected family leaders passed August validation. There is therefore no primary candidate to promote. September–October evaluation was still calculated for transparency, not used to rescue a failed design. Only two of all480 cells were positive in that later slice, by about5c and8c per contract; those are hindsight cells, not successful model selection.

This rejects these specific configurations on this record. It does not prove every possible BTC strategy is unprofitable, or that an entry rule must alone explain every profitable exit strategy.

WHAT WAS TESTED
480 configurations = six entries × five profit targets × four stops × four re-entry rules:
- Entry: always long, always short, follow15-minute momentum, follow60-minute momentum, prior60-minute range breakout, fade a30bp displacement from60-minute mean.
- Profit targets: 0.10%,0.25%,0.50%,1%,2% of entry price.
- Stop losses: 0.10%,0.25%,0.50%,1%.
- Re-enter immediately, wait15min, wait60min, or require a25bp pullback from the previous exit midpoint in the desired entry direction.
- One position at a time; maximum24h hold; no martingale, leverage or pyramiding.

SIZE AND ACCOUNTING
One whole Kalshi BTC perpetual contract represents0.0001BTC. In this archive it is approximately$6–$9 of exposure, close to the requested$10 scale but not falsely represented as exactly$10. Model starts each period with$100 cash, holds only one contract, and rejects new entries if available cash cannot cover the contract notional plus entry costs. Dollars in the tables are for that ONE contract; they are not per$100 notional or leveraged$10 margin. Performance across independently reset periods is not a continuously compounded single-account backtest. Marked drawdown is in dollars on the$100 model account; not a percentage of the small position.

DATA AND CHRONOLOGY
{a['source_rows']:,} archived minute records, {a['grid_minutes']:,} calendar minute slots, {a['valid_close_rows']:,} valid bid/ask closes. Raw coverage {a['start']} to {a['end']}. Actual Kalshi perpetual side quotes, not Coinbase spot proxies or binary prediction-market prices. Original arrays and funding records were reused without paid data downloads.

Development June4–July31, validation August, evaluation September1–October10; boundaries in UTC. Family leaders require at least50 development trades and are frozen by highest development total net. Validation advancement requires at least20 trades and positive net. No family leader passed. All development returns were already negative, independently enough to reject them.

The whole period has been examined in other research. This is a chronological split with an explicit selection procedure, not a pristine holdout. Publish every cell, including later lucky outcomes. Conditional intervals and selected leaders do not erase prior search/multiple-comparison effects.

EXECUTION AND COSTS
Signals use completed bars with contiguous history. Entry executes at the following minute's observed ask for longs/bid for shorts. Opposite-side bid/ask extrema detect targets/stops on subsequent bars. If both levels are touched in the same minute, stop takes precedence. A trigger schedules a market exit at the next valid minute quote: the simulator DOES NOT promise the stop or target price. Two-minute latency is separately stressed. No fills are invented through gaps; positions remain exposed until real quotes return. Pending exits take precedence over new entries. All positions are closed at each segment's last valid quote.

Primary fee assumption: base-tier taker12bp at entry AND exit, charged on each leg's actual notional. Actual bid/ask crossing already includes spread; add1bp per side execution friction. Actual archived signed funding is accrued only at timestamps entry<t<=exit. No invented funding averages. This is a published standard-tier fee scenario, NOT proof of the user's actual account tier or historical invoices. No temporary rebates or volume discounts assumed; zero-fee sensitivity is included as an upper diagnostic.

Fee source: https://www.cftc.gov/filings/orgrules/rules0608265590.pdf
Fee mechanics: https://help.kalshi.com/en/articles/16071417-perps-fees-explained
Contract size: https://help.kalshi.com/en/articles/15357587-btc-perpetual-futures-contract-specifications

The source has{a['invalid_extreme_fields']} invalid extreme fields on otherwise valid close bars. Those fields are not replaced with guessed highs/lows; the bar's valid close can still trigger an exit. Unavailable intrabar crossings can be missed. Invalid/sentinel close quotes are rejected using same-observation checks; no future-neighbor return filter was used. Account equity/drawdown relies on available minute quotes, not guaranteed subminute liquidation or executable depth. One fully collateralized contract avoids deliberately levering the signal, but is not a reconstruction of the exchange's historical risk engine.

An exact all-cost live replay is impossible from these candles: historical book depth/queue, actual fill latency/impact, fee invoice rounding and full risk-engine state are not present. Rather than claim those costs are known, test wider friction, delayed fills, cent-rounded fees and an optimistic resting-target alternative. Funding, both trading fees and observed spread are explicitly charged. Deposit/withdrawal charges and taxes are outside the strategy replay; no interest on idle cash assumed.

GRID RESULTS (dollars per one-contract model)
{table(counts)}

DEVELOPMENT-SELECTED FAMILY LEADERS — ALL PERIODS
{table(lead)}

THE SIMPLEST VERSION OF YOUR IDEA
Always buy; target+0.25%, stop−0.25%; immediately re-enter on the next eligible minute.
{table(naive[['period','trades','gross','fees','funding','slippage','net','win_pct','dd']])}

For September–October this made1,567 round trips. Price P&L was about−$0.14 before explicit fees/funding/slippage, while taker fees alone were about$30.57. Final modeled net was−$33.36. A0.25% target is already smaller than the approximate24bp round-trip fee plus2bp added friction, even before spread. Net win rate is therefore much lower than raw target-hit frequency. Re-entry repeats the cost; it does not reset the underlying economics.

NEAREST RECENT FAMILY LEADER, NOT A PASSED CANDIDATE
Mean-reversion entry, target2%, stop0.5%, wait60min. It lost$1.91 in development and$1.32 in validation. In evaluation its87 trades earned$1.6291 gross after spread, received$0.0121 funding, paid$1.6922 fees and$0.1410 additional friction: net−$0.1921. Gross gains are not the same as a usable edge.
{table(s.query('id==474 and period=="evaluation"')[['scenario','trades','gross','funding','fees','slippage','net','dd']])}

The maker-target sensitivity charges5bp on assumed target fills,12bp on entries/stops, and requires opposite-side quotes to move strictly beyond the target. This is a QUOTE-through fill assumption, not proof of traded volume or queue execution. It is deliberately optimistic; no maker-entry rebates are assumed. It still fails for every frozen leader in the later period. More latency worsens the closest rule substantially. Cent-rounding is an extra-cost scenario, not a claim about actual Kalshi invoice precision.

PASSIVE REFERENCE
One-contract long held across each period, same fee/friction/funding scenario:
{table(pd.read_csv(P/'passive_reference.csv'))}

Passive long earned about$0.32 in the evaluation window with one round trip. That is exposure to the observed BTC move, not a demonstrated predictive edge or recommendation. Flat cash earns$0 before interest and beats all frozen active leaders in that window.

UNCERTAINTY
Seven-day circular block bootstrap of daily marked P&L,5000 samples, for frozen leaders. Intervals are conditional on this already-searched history and short evaluation; no multiple-testing-adjusted claim. They are not evidence of fill accuracy.
{table(pd.read_csv(P/'uncertainty.csv'))}

VALIDATION
{v['ledger_rows_checked']:,} trade records across frozen-leader scenarios checked independently for priceP&L, signed funding, net accounting, strictly ordered entries/exits and nonoverlapping trades. Synthetic paths verify stop-first ordering, next-quote pricing, missing-quote delay and short-side funding/ask execution. End-equity reconciliation asserted on every grid/scenario replay. Input hashes retained in data_audit.json; complete grid retained in grid_results.csv; reproducible script study.py and validation script validate.py. Frozen-family and overall IDs were saved before later-period simulations. No production code, service, ledger, order or stable checkpoint was modified.

CONCLUSION
No build/paper/live promotion from these480 rules. Wider targets and less frequent trading reduce fee damage but do not produce a selection-valid net advantage here. The evidence does not support promising a repeated$10-position income loop. Any new mechanism would require its own declared test, and must outperform these failures after costs rather than merely having attractive gross trades.
'''
(P/'REPORT.txt').write_text(text)
manifest={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file() and f.name!='MANIFEST.json' and '__pycache__' not in f.parts};(P/'MANIFEST.json').write_text(json.dumps(manifest,indent=2))
print(P/'REPORT.txt')
