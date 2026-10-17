from pathlib import Path
import json,datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
rows=json.loads((P/'ledger.json').read_text());s=json.loads((P/'results.json').read_text());levels=json.loads((P/'thresholds.json').read_text());depth=json.loads((P/'depth_subset.json').read_text());audit=json.loads((P/'independent_audit.json').read_text())
def money(x):return 'unknown' if x is None else f'{x:+.4f}'
def label(x):return 'N/A' if x is None else f'{x:+g}'
losses=[r for r in rows if not r['won']];wins=[r for r in rows if r['won']]
low=s['low']['all'];high=s['high']['all']
lines=[
'MARKET-SPECIFIC Z STOPS — ALL LOSSES AND WINNERS, NO TIME FILTERS',
'Fixed historical cohort: September12–October14, markets closed through22:00ET.',
'461 actual settled trades:438 wins and23 losses; actual mixed historical sizes.',
'This updates the older459-trade sample with the WTI21:30 loss and22:00 winner.',
'Hold-to-settlement net: +$28.5536. All entries and charged entry fees unchanged.',
'',
'FINDING',
'The fixed winner-derived thresholds cross on17losers and0winners. One crossing',
'is too late to execute with the modeled1second order latency. Zero winners cut',
'is in-sample by construction: levels were chosen below these same winners\' minima.',
'It is not independent proof that future winners will be protected.',
'',
'In the minute-bid-low stress scenario, six losses are reduced by$6.1024 after',
'exit fees, or$5.8324 after also charging1cent per exited contract as friction.',
'Combined net becomes+$34.6560 fees-only, or+$34.3860 including friction, versus',
'+$28.5536 holding. No loss becomes profitable in this stress scenario.',
'THIS IS A QUOTE-BASED SCENARIO, NOT VERIFIED EXECUTABLE BACKTEST P&L. Historical',
'one-second source prices are complete; continuous sell-side depth is not.',
'The true all-history fillable savings and counterfactual net remain unverified.',
'',
'AFTER-FEE QUOTE SCENARIOS (NOT CONFIDENCE INTERVALS OR GUARANTEED BOUNDS)',
'Scenario                Losses reduced  Winners cut   Saved fees-only  Saved +friction   Combined net +friction',
]
for name,desc in [('low','Minute bid low'),('high','Minute bid high'),('stale_open_proxy','Stale minute open')]:
    q=s[name]['all'];lines.append(f'{desc:24s}{q["losses_reduced"]:14d}{q["winners_cut"]:13d}{money(q["fees_only_loss_savings"]):>18s}{money(q["loss_savings"]):>17s}{money(q["net"]):>25s}')
lines += [
'The minute high or opening bid can precede the stop crossing. Do not take the',
'optimistic+$36.9810 incremental result as obtainable. Examples:BTC08:30 onOct14',
'crosses with2seconds left while its enclosing minute once quoted98.7c; NATGAS',
'crosses11seconds before close while the minute high was99c. Those high quotes',
'do not show an order could sell there after the signal.',
'Even the bid-low scenario assumes enough quantity at a nonzero bid. A zero',
'bid results in no assumed sale and holding to settlement in that scenario.',
'',
'MARKET RESULTS — FIXED THRESHOLDS; MINUTE-BID-LOW SCENARIO',
'Asset      Stop z  Losses  Crossings  Losses reduced  Saved fees-only  Saved +friction  Winners cut  Hold net  Scenario net',
]
for a,z in levels.items():
    q=s['low']['assets'][a]
    lines.append(f'{a:10s}{label(z):>7s}{q["losses"]:8d}{q["losses_crossing"]:11d}{q["losses_reduced"]:16d}{money(q["fees_only_loss_savings"]):>17s}{money(q["loss_savings"]):>17s}{q["winners_cut"]:13d}{money(q["hold"]):>10s}{money(q["net"]):>14s}')
lines += [
'USD/CAD has0winners, so there was no winner-derived level. Its-$4.3896 loss is',
'retained in the full hold and exit comparisons. The separately labeled neutral',
'z=0 diagnostic does not trigger: minimum z remains+0.1891. Nothing saved.',
'EUR/USD has27winners and0losses, so its boundary has no loss-rescue evidence.',
'',
'EXACT DOLLAR RECONCILIATION — MINUTE-BID-LOW SCENARIO',
f'Actual profits from438winners: {money(sum(r["hold_net"] for r in wins))}',
f'Actual total23losses:         {money(sum(r["hold_net"] for r in losses))}',
'Actual net:                 +28.5536',
'Six modeled sales, gross:     +6.3950',
'Modeled exit fees/rounding:   -0.2926',
'Cash recovered after fees:    +6.1024',
'27contracts x1cent friction:  -0.2700',
'Final reduction in losses:    +5.8324 (6.17% of the original$94.5909 lost)',
'Remaining losses:           -88.7585',
'Winner damage:                0.0000',
'Combined scenario net:      +34.3860 =28.5536+5.8324',
'Entry fees are already included in actual holding P&L. They are not charged twice.',
'All monetary results use actual quantities, including historical1/2contract',
'entries. They are not a synthetic five-contract return for every old trade.',
'',
'MATCHED CONTINUOUS-DEPTH CHECK',
'20 actualBTC/ETHtrades have archived source first arrivals and full held-side',
'book depth. They contain18winners and2losses. Under these same asset thresholds:',
'money saved$0; winners cut0; net-$2.8977, exactly the same as holding.',
'BTC Oct3 19:30 first crosses-8.5 with1second left;1second execution latency puts',
'the order at close, so no modeled fill. BTC Oct4 02:30 never reaches-8.5.',
'This confirms that the very deep BTC threshold does not rescue those two losses.',
'This20-trade result is not a substitute for missing books on the other441trades.',
'',
'EVERY LOSS — SAME FIXED RULE, NO EXCLUDED LOSSES',
'Ticker | Quantity | Actual loss | Threshold | Minimum z | Seconds left at first breach | Quote-low sale cents | Cash saved after fees | Cash saved after fees+friction | Optimistic minute-high cash saved',
]
for r in losses:
    lo=r['scenarios']['low'];hi=r['scenarios']['high'];rem=r['close']-r['trigger'] if r['trigger'] is not None else None
    px=f'{100*lo["price"]:.3f}' if lo['price'] is not None else 'no modeled sale'
    lines.append(f'{r["ticker"]} | {r["quantity"]:g} | {r["hold_net"]:.4f} | {label(r["threshold"])} | {r["min_z"]:+.4f} | {rem} | {px} | {lo["fees_only_delta"]:.4f} | {lo["delta"]:.4f} | {hi["delta"]:.4f}')
lines += [
'',
'REPLAY DEFINITION',
'One-second checks; no minimum holding period, confirmation, time-of-day or',
'expiry filter. First strict signed held-side z<threshold crossing. Entry side,',
'reference and volatility frozen. CryptoCFmoves anchored to entryCoinbase spot;',
'financials use recorded Pyth prices. Assumed1second source availability. These',
'are reconstructed z paths, not complete recorded live z. One-second order',
'latency is operational execution modeling; no order may fill at/after close.',
'Exit fee model is the same prior conservative taker coefficient0.07*q*p*(1-p),',
'rounded up to1e-6per assumed fill, proceeds rounded down to1e-4. Actual charged',
'entry fees retained. Exit fees are modeled, not exchange-charged receipts.',
'Quote scenarios take held-side bidOHLC for the minute containing modeled sale.',
'No interpolation or fabricated one-second order books. Closed/no-positive-bid',
'scenario retains holding; missing quote would remain unknown. No missing source',
'or quoted minute data in these461rows. Full exit quantity at that bid is assumed.',
'Finer0.2secondcrypto ticks add2BTCcrossings only in the last0.2seconds; neither',
'leaves1second to execute. TheOct3BTCcrossing can be0.2seconds earlier, but this',
'does not validate any extra fill; finest sensitivity is saved separately.',
'',
'INTERPRETATION',
'These sample-minimum thresholds protect the sampled winners by construction,',
'but often wait until very little value remains. The conservative quote scenario',
'rescues only6.17% of lost dollars. BTC-8.5 is particularly ineffective. ETH0',
'produces the largest saving in that stress case($2.9023); only3ETHlosses exist.',
'Do not deploy this as a validated stop or restart paused lanes from these data.',
'No parameters were retuned on the losses. No time variants tested.',
'',
'AUDIT',json.dumps(audit,indent=2),
'',
'FILES',
'ledger.json: all461individual path/threshold/quote/P&L results.',
'orders.json: read-only frozen ledger; actual_entries.json: normalized inputs.',
'results.json: per-asset/per-lane/full-cohort comparisons for all quote scenarios.',
'depth_subset.json: separate20-trade continuous-book result and individual receipts.',
'usdcad_z0_diagnostic.json: non-winner-derived USD/CAD zero-line check.',
'finer_crypto_loss_checks.json: additional0.2secondsource sensitivity.',
'prepare.py/replay.py/audit.py and source hashes provide reproduction.',
'Live/paper bots, strategies, price caps, sizes, risk limits and services unchanged.'
]
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print('Report written',len(rows),'trades')
