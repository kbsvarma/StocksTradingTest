"""Readable report from saved, audited research outputs."""
from pathlib import Path
import json, gzip, datetime

P = Path(__file__).resolve().parent
d = json.loads((P/'detailed_results.json').read_text())
selection = json.loads((P/'selection.json').read_text())
configs = json.loads((P/'configs.json').read_text())
metrics = json.loads((P/'metrics.json').read_text())
grid = {c['id']: m for c, m in zip(configs, metrics)}
ledgers = json.load(gzip.open(P/'selected_ledgers.json.gz', 'rt'))
depth = json.loads((P/'recorded_depth.json').read_text())
lines = []
def w(s=''): lines.append(s)
def money(x): return f'${x:+.4f}'
def row(label, m):
    w(f"{label:36} {m['losses_reduced']:3} {m['loss_savings']:10.4f} {m['winners_cut']:4} {m['winner_cost']:11.4f} {m['net']:11.4f} {m['delta']:11.4f}")
def table_header():
    w('Design                               Losses    Saved $  Cuts  Winner cost$      Net $     Change $')

w('TIMED Z EXITS — RESEARCH RESULTS, 14 OCTOBER 2026')
w('Frozen actual-entry cohort: 12 September through 14 October, 22:00 ET.')
w('Live bot unchanged. No orders, service restarts, entry changes or lane reactivation.')
w()
w('FINDING')
w('Market-specific timing improves this historical quote comparison: $16.1576 recovered across 9 of 23 losing trades, no sampled winners cut, total net $44.7112 versus holding $28.5536. Previous no-time market floors recovered $5.8324. These exits reduce losses; none of the nine becomes profitable.')
w('However, all improvement occurs in the earlier selection period. The unchanged rules do not exit or save any of the seven losses in the later 111 trades. Raising all selected thresholds another 0.25 cuts seven winners and leaves only $0.6873 improvement. This is a sensitive research candidate, not a proven stop rule for the current bot.')
w()
w('MAIN COMPARISON — HISTORICAL MINUTE-BID-LOW SCENARIO, NOT VERIFIED FILLS')
w('Saved = sale proceeds recovered from original losing trades, AFTER modeled exit fees and 1 cent per contract friction. Winner cost includes both forfeited settlement profit and any new realized loss. Net includes actual entry fees and modeled exit costs. All dollar amounts USD.')
table_header()
names = [
    ('Hold to settlement', 'hold'), ('Previous market floors', 'previous_market_floors'),
    ('Market rules: zero sampled cuts', 'development_asset_0'),
    ('Market rules: higher BTC threshold', 'development_asset_0.02'),
    ('Market rules: higher BTC + ETH', 'development_asset_0.05'),
    ('Same zero-cut levels, remove timing', 'zero_cut_map_no_time_rules'),
    ('Same timing, all levels +0.25', 'zero_cut_map_z_plus025'),
    ('Common rule selected on early data', 'development_common_0'),
]
for label, name in names: row(label, d[name]['periods']['full'])
w('Zero-cut arithmetic: unchanged winner profit $123.1445 minus remaining loss $78.4333 = $44.7112. Original losses totaled $94.5909. $16.1576 recovery is 17.1% of those total loss dollars.')
w('Cash recovered before exit costs $17.1350, less exit fees/cash rounding $0.5574 and friction $0.4200 = $16.1576. Fees-only total net is $45.1312; the headline $44.7112 also subtracts friction.')
w('The two-winner-cut version saves $1.6852 more from losses but costs $1.2528 on winners: only $0.4324 extra overall. Raising ETH as well adds only $0.0002 overall.')
w()
w('EXACT MARKET RULES FOR THE ZERO-SAMPLED-CUT CANDIDATE')
w('Check once per second. All z thresholds are signed absolute levels in the held direction, NOT drops from entry. A negative z means the underlying has crossed the reference against the held side. Strict comparison: z < threshold.')
w('BTC: z < -0.50 continuously for 5 seconds, with at least 120 seconds remaining when the exit triggers.')
w('ETH: z < 0 immediately; no holding-period restriction.')
w('SOL: z < -0.50 immediately; no holding-period restriction.')
w('COPPER: after holding 15 seconds, require z < 1.00 continuously for 5 seconds; trigger only within the first 120 seconds after entry. Earliest possible trigger is about 20 seconds after entry.')
w('WTI: z < -1.00 immediately, with at least 120 seconds remaining.')
w('XRP, NATGAS, EURUSD, USDCAD: retain holding. Fewer than two development-period losses, or fewer than ten trades, prevented fitting a new rule; this is lack of evidence, not proof that no exit can help.')
w('Confirmation starts only after all eligibility conditions hold, resets on recovery or ineligibility, and uses elapsed time. Trigger must precede settlement enough to accommodate one-second order latency. One modeled attempt at the first trigger; no optimized later retries.')
w('Higher-BTC version: replace only BTC with z < +0.25 immediately within the first 120 seconds after entry. Higher-BTC-and-ETH version also replaces ETH with z < +1 within that same first 120 seconds.')
w()
w('ZERO-CUT CANDIDATE BY MARKET')
w('Market   Trades W/L         Hold $    Saved $  Losses reduced  Winner cuts     Net $')
for a, m in d['development_asset_0']['assets'].items():
    w(f"{a:8} {m['trades']:4} {m['wins']:3}/{m['losses']:<3} {m['hold']:12.4f} {m['loss_savings']:10.4f} {m['losses_reduced']:10} {m['winners_cut']:12} {m['net']:12.4f}")
w('Copper remains paused. Its $6.9112 is 42.8% of the fitted improvement, from only two losing trades among 15 historical trades. Excluding Copper, modeled recovery is $9.2464; this is not evidence to reactivate it. USD/CAD remains paused.')
w('Both original and premium lanes were included. Original: 398 trades, 375 wins/23 losses, hold $18.8008 -> $34.9584. Premium: 63 wins/0 losses, unchanged $9.7528. No empirical loss sample supports a premium-specific stop.')
w()
w('WHAT TIMING CHANGED, HOLDING THE SELECTED LEVELS FIXED')
table_header()
for label, name in [('All selected timing', 'development_asset_0'), ('No confirmation delay', 'zero_cut_map_no_confirmation'), ('No minimum holding time', 'zero_cut_map_no_minimum_hold'), ('No exit window restriction', 'zero_cut_map_no_exit_window'), ('Remove every timing restriction', 'zero_cut_map_no_time_rules'), ('All selected levels -0.25', 'zero_cut_map_z_minus025'), ('All selected levels +0.25', 'zero_cut_map_z_plus025')]:
    row(label, d[name]['periods']['full'])
w('These are matched historical comparisons, not independently validated incremental effects.')
w()
w('HIGHER COMMON Z THRESHOLDS')
w('Apply the same stop to every asset, wait at least 30 seconds after entry, then require 10 continuous seconds below the threshold. No expiry-window restriction. Prices/fees as in the main table.')
table_header()
for z in [0, .5, 1, 1.5, 2, 2.5, 3]:
    row(f'z < {z:g}, hold30s, confirm10s', grid[f'z{z:g}_g30_p10_all']['full'])
w('Higher stops rescue more losing stake but liquidate many eventual winners at losses. A cut winner can cost much more than its small original settlement profit.')
w()
w('CHRONOLOGICAL CHECK')
w('Selection used only trades closed before 12 October 00:00 ET: 350 trades, 334 wins/16 losses. Later check: 111 trades, 104 wins/7 losses through 14 October 22:00 ET. Earlier conversations have already inspected this history, so it is not a pristine unseen holdout.')
for name in ['development_asset_0', 'development_asset_0.02', 'development_common_0']:
    w(name)
    for period in ['development', 'later']:
        m = d[name]['periods'][period]
        w(f"  {period}: hold {money(m['hold'])}, net {money(m['net'])}, change {money(m['delta'])}, saved {money(m['loss_savings'])}, winners cut {m['winners_cut']}.")
w('Market-specific candidates produce no later exits. BTC/WTI do not cross their selected thresholds for the required duration inside the allowed window. NATGAS/USD-CAD have no fitted stop owing to insufficient earlier loss samples. We did not retune to these later failures.')
w('A common rule chosen from the earlier period (z < -0.5 for five seconds, >=120 seconds remaining) worsens later net by $2.4892: $1.3765 rescued versus $3.8657 damage to one eventual winner.')
w()
w('RECORDED ORDER-BOOK CHECK — SEPARATE 20-TRADE BTC/ETH SAMPLE')
w('3–4 October: 18 winners, two losses, actual holding net -$2.8977. These trades are included in the full cohort; this is an execution-evidence check, not an independent statistical sample. Recorded CF first arrivals, one-second decisions, full held-side bid depth before and after one-second latency, worse cash across the two books, then modeled fees and 1 cent per contract friction. No actual exits were sent.')
table_header()
for label, name in [('Holding', 'hold'), ('Previous market floors', 'previous_market_floors'), ('Market rules: zero sampled cuts', 'development_asset_0'), ('Market rules: higher BTC threshold', 'development_asset_0.02'), ('Common z1, hold30s, confirm10s', 'z1_g30_p10_all')]:
    row(label, depth[name]['metrics'])
for name in ['development_asset_0', 'development_asset_0.02']:
    w(name+' modeled exits:')
    for x in depth[name]['ledger']:
        if x['exit']:
            w(f"  {x['ticker']}: {x['quantity']:g} contracts at average {x['price']*100:.2f}c, hold {money(x['hold_net'])}, exit net {money(x['net'])}, recovered {money(x['delta'])}, exit fees/rounding ${x['fee']:.4f}, friction ${x['friction']:.4f}.")
w('These two old BTC losses did not pass today\'s original BTC z3 entry gate. This is an exit study on historical entries and cannot be presented as extra earnings available to the current entry design. Recorded-depth coverage exists for only these 20 trades; full-cohort executable P&L is unknown.')
w()
w('EXECUTION AND PARAMETER SENSITIVITY')
for name in ['development_asset_0', 'development_asset_0.02']:
    w(name)
    for mode, v in d[name]['sensitivity'].items():
        m = v['full']; w(f"  {mode}: net {money(m['net'])}, change {money(m['delta'])}, saved {money(m['loss_savings'])}, winner cost {money(m['winner_cost'])}.")
w('Minute highs can occur before the trigger. Neither minute-low nor minute-high results are guaranteed executable bounds; neither provides quantity, queue position or exact contemporaneous bid. The large change between quote assumptions is itself an execution-data limitation.')
w('Exploratory two-day block resampling of 33 calendar days gives a 95% incremental interval of $1.8660 to $37.1558 for the zero-cut map. This reuses selected historical parameters, is unadjusted for the search and does not establish an edge. The later-period zero benefit and nearby-threshold instability are more decision-relevant.')
w()
w('SEARCH AND DATA DEFINITIONS')
w('3,723 configurations: absolute z from -1 through +3, minimum holds 0/15/30/60/120 seconds, confirmation 0/2/5/10/20/30/60 seconds, all/early/late holding and expiry windows, plus simple time-varying thresholds and entry-drop controls. Full grid and all failures retained. Search range was extended after the first pass; this is disclosed in PLAN.txt.')
w('No winning/losing trade was dropped. Original actual quantities, costs and entry fees retained, including historic 1/2/5-contract sizing and partial fills. This is not a synthetic five-contract replay. The previous full-period winner floors are only a comparison; excluded from chronological selection to avoid using later winner minima.')
w('Signed z = held side * (spot/reference - 1) / (entry sigma * sqrt(minutes remaining)). Freeze entry reference and sigma. Crypto: historical CF changes anchored to recorded Coinbase entry spot. Financials: historical Pyth. Full-history sources assume availability one second after timestamp, not observed first-arrival latency. Actual received CF is used only in the separate recorded-depth check.')
w('Full-history execution uses the held-side bid low in the minute containing trigger+1s. NO prices use complements of YES asks, with high/low inverted. Positive bid assumed available for full historical quantity only as a quote scenario; zero bids retain holding. This use of minute OHLC is an ex-post execution sensitivity, not an executable tick replay.')
w('Modeled exit fee is ceil(0.07*q*p*(1-p), 1e-6) per level, cash rounded down to 1e-4; add 1c per contract friction. Entry fees are actual. Reported exit-fee amounts include cash-rounding residue.')
w()
w('TRADE-LEVEL EXITS FOR THE ZERO-CUT MAP — MINUTE-LOW SCENARIO')
w('Ticker                            Qty  Age(s) Left(s)       z  Bid(c)    Held net    Exit net     Saved')
for x in ledgers['development_asset_0']:
    if x['exit']:
        w(f"{x['ticker']:33} {x['quantity']:4g} {x['trigger']-x['entered']:7.1f} {x['close']-x['trigger']:7.0f} {x['trigger_z']:7.3f} {x['price']*100:7.2f} {x['hold_net']:11.4f} {x['net']:11.4f} {x['delta']:9.4f}")
w()
w('VALIDATION AND PRESERVATION')
w('8,264 selected/random grid results matched an independent scalar trigger implementation. 566 independent Decimal quote-exit cash checks and nine recorded-depth cash checks passed. All 461 actual ledger cash identities reconcile. Saved production check confirms 25 code/style files, ARM/HALT flags and five service states/PIDs unchanged. Stable return-point checkpoint not edited.')
w('Artifacts: PLAN.txt; grid.py; details.py; audit.py; configs.json; grid_arrays.npz; metrics.json; selection.json; detailed_results.json; selected_ledgers.json.gz; recorded_depth.json; manifest.json; scalar_audit.json; cash_and_preservation_audit.json. Original source datasets stay in the preceding experiment directories identified by manifest hashes. Run with the server Kalshi Python environment; no production trading code is imported.')
w('Decision: retain the market-specific timed map as a research candidate; do not deploy it or reactivate paused lanes from this study. Broad higher z exits are rejected by the winner damage. Prospective paired books across the actual current-entry cohort would be needed to establish a tradable benefit; this task starts no new collector.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(P/'REPORT.txt')
