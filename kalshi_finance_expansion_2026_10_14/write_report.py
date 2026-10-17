"""Generate the final comparison from saved results, never from hand-entered PnL."""
import json,gzip,datetime as dt
from pathlib import Path
P=Path(__file__).resolve().parent
read=lambda n:json.loads((P/n).read_text())
fx=read('fx/results.json');idx=read('indices_results.json');older=read('older_indices_results.json');freq=read('frequency_uncertainty.json');combo=read('combined_results.json');inv=read('inventory.json');validation=read('validation.json');preserve=read('preservation_after.json')
lines=[]
def add(s=''):lines.append(s)
def metric(m):return f"{m['trades']} trades; {m['wins']}W/{m['losses']}L; net ${m['net']:+.4f}; drawdown ${m['drawdown']:.4f}"
add('KALSHI FINANCE EXPANSION — FINAL RESEARCH, October 14, 2026')
add('Research only. Existing trading bots unchanged. All results below are simulated five-contract trades, not actual account profits.')
add()
add('DECISION')
add('1. USD/CAD 15-minute, using the existing EUR time-adjusted cushion, is the strongest next PAPER candidate for adding frequency. It remains profitable in the new extension, at twice the modeled friction, at the price cap, and with a one-minute delay. Its short history and unproven fills do not justify claiming a proven edge or immediate live activation.')
add('2. Dow hourly, using the same time-adjusted cushion, is a secondary diversification candidate. Positive in five weekly blocks, but only about 1.6 trades per calendar day over this sample; the fresh extension added just two trades. It is not the main frequency solution.')
add('3. Defer AUD/USD expansion: the new period lost money and an extra 1c/contract nearly removes its aggregate profit. Reject GBP/USD and USD/JPY adaptive variants. Nasdaq adaptive is positive but sparse and was negative in the older slice; S&P variants fail. Treasury yields remain unverified, not zero-PnL strategies.')
add()
add('PRIMARY AFTER-FEE NUMBERS')
add('Use 5 contracts per entry, initial modeled cash $100, full taker fees plus 1c/contract extra execution cost. Buy at the worse of signal and subsequent recorded ask, subject to the original cap. One position per underlying and one entry per expiry. These are quote proxies; historical depth/queue position and source first-arrival times are unavailable.')
add('All rows below use the same previously specified time-adjusted cushion. Each row is a separate portfolio. Different dates are explicitly shown.')
add('Market | Period through Oct14 noon ET | Trades | W/L | Net | Drawdown | Trades/calendar day | Net with 2c/contract cost')
summary=[]
for a,title in [('USDCAD','USD/CAD 15m'),('AUDUSD','AUD/USD 15m'),('USDJPY','USD/JPY 15m'),('GBPUSD','GBP/USD 15m'),('EURUSD','EUR/USD 15m, existing control')]:
 m=fx['eurusd_adaptive']['quote1c'][a]['full'];stress=fx['eurusd_adaptive']['quote2c'][a]['full'];days=freq[a]['calendar_elapsed_days'];period='Oct7–14 (6.77 elapsed days)' if a in ('AUDUSD','USDCAD') else 'Sep23–Oct14'
 add(f"{title} | {period} | {m['trades']} | {m['wins']}/{m['losses']} | ${m['net']:+.2f} | ${m['drawdown']:.2f} | {m['trades']/days:.2f} | ${stress['net']:+.2f}")
 summary.append(dict(market=title,period=period,**{k:m[k] for k in ('trades','wins','losses','net','drawdown')},calendar_rate=m['trades']/days))
for a,title in [('DJI','Dow hourly'),('INX','S&P hourly/daily combined'),('NDQ','Nasdaq hourly/daily combined')]:
 m=idx['eur_curve']['quote1c'][a];stress=idx['eur_curve']['quote2c'][a]
 add(f"{title} | Sep14–Oct14 (30.5 elapsed days) | {m['trades']} | {m['wins']}/{m['losses']} | ${m['net']:+.2f} | ${m['drawdown']:.2f} | {m['trades']/30.5:.2f} | ${stress['net']:+.2f}")
 summary.append(dict(market=title,period='Sep14–Oct14',**{k:m[k] for k in ('trades','wins','losses','net','drawdown')},calendar_rate=m['trades']/30.5))
add('AUD/CAD began Oct7 at17:30ET. Their ~10/day calendar rates include the weekend and partial dates. Across four complete listed weekdays (Oct8,9,12,13), AUD averaged13.75 and CAD14.25/day. These four days are not a reliable long-run frequency estimate. Daily index range/up-down contracts share the hourly closing expiry and do not create new independent windows.')
add()
add('NEW DATA, COSTS AND TIMING')
for a in ['USDCAD','AUDUSD','USDJPY','GBPUSD']:
 add(a+' new Oct12 00:00–Oct14 12:00ET: '+metric(fx['eurusd_adaptive']['quote1c'][a]['extension']))
 for sc in ['quote2c','cap1c','delay60_1c']:add('  '+sc+': '+metric(fx['eurusd_adaptive'][sc][a]['full']))
add('The delayed scenario selects a different surviving set of trades. It is not the PnL of the original 68 CAD entries held one minute later. CAD drops from68 to20 entries and its new-extension delayed sample is small. Quote-positive results cannot establish that all68 would have filled. Missing inputs and expired entries stay rejected.')
add('Dow adaptive: '+metric(idx['eur_curve']['quote1c']['DJI'])+'; 2c net $'+str(idx['eur_curve']['quote2c']['DJI']['net'])+'; delayed '+metric(idx['eur_curve']['delay60_1c']['DJI']))
add('Dow prior to Oct12: '+metric(idx['eur_curve']['quote1c']['DJI']['prior'])+'; new extension: '+metric(idx['eur_curve']['quote1c']['DJI']['extension']))
add('Dow weekly net: '+str(idx['eur_curve']['quote1c']['DJI']['weekly']))
add()
add('WHAT WOULD BE ADDED TO THE CURRENT BOT?')
add('Matched Oct8 00:00ET to Oct14 before11:30ET, 6.479 elapsed days. Full fresh-signal opportunity replay using current original BTC z3 and all other existing rules. This is NOT pruning a trade ledger. No live balances were queried: a pooled $100 is assumed; account/engine routing and earmarked cash need verification before a future deployment.')
add('Shared maximum25 contracts/$25 reserved exposure/$20 daily loss including open positions. Fees+1c. No exposure or size increase.')
for key in ['incumbent','plus_CAD','plus_Dow_curve','plus_CAD_Dow_curve','plus_AUD','plus_AUD_CAD']:
 m=combo['quote1c'][key];add(key+': '+metric(m)+(f"; extra {m['net_trade_change']} trades, ${m['net_pnl_change']:+.4f} net" if 'net_trade_change' in m else ''))
add('CAD alone adds65 trades (~10.0/calendar day;30.5% more than213 incumbent entries), with no incumbent trade displaced in this replay. Combined drawdown rises from$9.10 to$11.36. CAD+Dow adds72, but only7 come from Dow in the matched recent period.')
for sc in ['quote2c','cap1c','delay60_1c']:
 m=combo[sc]['plus_CAD'];add(f"CAD incremental {sc}: {m['net_trade_change']} extra trades, ${m['net_pnl_change']:+.4f} net.")
add('At a full-minute delay only19 incremental CAD entries remain (~2.9/day). Thus the ~10/day opportunity estimate depends on prompt execution. FX positions also add correlated USD exposure; these are not independent diversified bets.')
add()
add('FROZEN DESIGNS AND ALL VARIANTS')
add('FX strict: original z2 / premium z3,3–10min. Adaptive: original z=max(0.5,1.75+0.5*log2(minutes/5)); premium same with base2.75,1–10min. Original signal85–<95c, execution cap93.9c; premium93–<98c, cap97.5c; spread<=2c. Whole-cent market ticks make the effective fixed maxima93c/97c. No new z grid was fitted.')
add('Indices: same strict and adaptive profiles, plus predeclared5m and15m probability entries. Prior60 completed minute returns; volatility inflated1.25; terminal normal probability blended50% with market midpoint; buy50–<95c only when modeled probability exceeds ask+fee+1c by>=3c. Range probability is the CDF difference. Cushion clears the nearest adverse boundary. Require5 contracts traded in the preceding5 minutes; this is a liquidity screen, not depth proof. Choose one strike using decision information before inspecting the future book proxy.')
for a in ['USDCAD','AUDUSD','USDJPY','GBPUSD']:
 add(a+' strict control: '+metric(fx['strict_z2_z3']['quote1c'][a]['full']))
for a in ['DJI','INX','NDQ']:
 for prof in idx:
  add(a+' '+prof+': '+metric(idx[prof]['quote1c'][a]))
add('Older cached August–Sep11 sensitivity, same four new mechanisms, threshold families only (no newly fetched older range series):')
for a in ['DJI','INX','NDQ']:
 for prof in older:add(a+' '+prof+': '+metric(older[prof]['quote1c'][a]))
add('Earlier Dow adaptive had only3 trades/+1.3529. Nasdaq adaptive previously21 trades/20W1L/−0.4398. Reused historical data and model comparisons are not an untouched holdout. No unseen losses can be inferred away from a 50-win sample.')
add()
add('CURRENT MARKET AVAILABILITY AND VOLUME')
add(f"Scanned all {inv['recurring_series']} Financials series labeled daily/hourly/fifteen_min from1,004 Financials definitions. {sum(r.get('recent_expiries',0)>0 for r in inv['rows'])} had settled events in the trailing week; {sum(r.get('contracts_7d',0)>0 for r in inv['rows'])} had nonzero volume. No scan errors. Company-category catalog has no daily/hourly/15-minute definitions. This is a recurring-finance review, not a claim that every monthly/one-off financial contract was backtested.")
for s in ['KXINXU','KXNASDAQ100U','KXDJI','KXEURUSD15M','KXGBPUSD15M','KXUSDJPY15M','KXAUDUSD15M','KXUSDCAD15M','KX2YRRATEH','KX5YRRATEH','KX7YRRATEH','KX10YRRATEH','KX30YRRATEH']:
 r=next(r for r in inv['rows'] if r['series']==s);add(f"{s}: {r['recent_expiries']} expiries; {r['positive_volume_expiries']} with volume; {r['contracts_7d']:,.2f} contracts traded; median{r['median_contracts_expiry']:,.2f}/expiry.")
add('Volume is contracts, not dollars, and strike volumes within an index expiry are aggregated. S&P/Nasdaq/Dow15-minute and Treasury15-minute definitions had no open markets/recent settlements. Several Treasury products tagged daily actually settled only once during the week. The hourly yield series have much thinner activity (median100–354contracts/expiry), and all five sampled historical source-chart requests returned404. No valid yield z-backtest is claimed.')
add()
add('DATA QUALITY / EXECUTION EVIDENCE')
add('FX:5,057 settled market histories,1,270 hourly source charts selected; all retrieved without errors, zero rounded source/outcome mismatches, no overlapping-source conflicts. Missing reference/sigma/quote minutes excluded. Current extension adds240 completed15-minute markets per pair to the Oct12 study. Current primary candles exclude the noon cutoff market; the trailing-week inventory query includes the boundary, hence448 versus449 AUD/CAD windows in those differently defined counts.')
add('Indices:156 hourly expiries per underlying and22 events per each of four daily families. All requested chart/candle fetches succeeded;23 S&P and25 Nasdaq hourly events lacked a complete61-point source sequence and remain excluded. Daily families were fully evaluable but overlap closing hourly expiries. Source charts around expiry differ from official settlement by>0.01 index points in227/508 audited events (largest absolute gap11.274points); timestamps, rounding and source definitions differ. Official exchange outcomes, not chart-reconstructed winners, determine PnL. Future paper validation must measure source basis and publication delay.')
add('Fresh bounded order-book observations are saved. One USD/CAD snapshot had89c NO ask/87c bid and80contracts within the exact90c cap. Its preceding snapshot had a3c spread and failed the spread rule. These snapshots check availability, not full z/time qualification and not actual fills. A Dow book moved93c→94c within2seconds, crossing the original whole-cent cap; an S&P example lost most displayed depth between snapshots. Initial book probes used cached open-market lists and included expired FX markets; these were retained but excluded from fresh-book conclusions. Corrected probes force uncached listings and check expiry. No paper or live orders were submitted.')
add()
add('UNCERTAINTY')
for a in ['USDCAD','AUDUSD']:
 u=freq[a]['profiles']['eurusd_adaptive']['uncertainty'];add(f"{a}: average all-in break-even win rate {u['breakeven']:.2%}; Wilson95 win interval {u['wilson_win95'][0]:.2%}–{u['wilson_win95'][1]:.2%}; exploratory calendar-day bootstrap net95 ${u['day_block_net95'][0]:.2f} to ${u['day_block_net95'][1]:.2f}.")
add('Both intervals include an unprofitable true edge. Day resampling preserves within-day clustering but does not adjust for repeated model selection, cross-day dependence or unseen shocks. CAD has only one observed loss; two extra similar losses would erase the current net. The recommendation is to validate CAD execution prospectively, not to assume the backtest return will persist.')
add()
add('VALIDATION / PRESERVATION')
add(f"Forty old-convention replay controls reproduced the Oct12 results exactly. Final independent checks: {validation['checks']['fx_rows']} FX rows, {validation['checks']['index_rows']} index rows and {validation['combined_rows_independently_checked']} combined rows across overlapping scenarios, including fees, chronology, price caps, one-asset/expiry ownership, cash, exposure and daily risk. These row counts are validation work, not independent trades.")
add('The final validation explicitly rejects any worse-of-signal/subsequent quote above the cap. The earlier FX convention clipped that case to the cap; its reproducible outputs remain in fx/reproduced_prior_convention. This change removes one adaptive JPY winner and leaves the CAD/AUD primary results and final combined totals unchanged. Rejected intermediate combined opportunity rows are retained in provisional_before_validation. No strategy parameters were selected to remove a losing outcome.')
add(f"Preserved {len(preserve['files'])} production/checkpoint source files, {len(preserve['flags'])} arming/halt flags, and all5 service identities/PIDs/states. Live crypto, dashboard, execution observer and tennis remain as found. Weather paper remains inactive/disabled and canceled. No order, transfer, activation, restart or ongoing finance collector.")
add()
add('FILES / REPRODUCIBILITY')
add('PLAN.txt was written before new outcomes were fetched. common.py and inventory.py archive public GET receipts. fx/{pull,build,analyze,simulation}.py extend the prior frozen FX study. pull_indices.py and analyze_indices.py rebuild index entries. engine.py and combined.py enforce shared portfolio accounting. verify.py and finalize.py audit results. All losing variants, full ledgers, source audits, book receipts and preservation manifests are retained.')
add('Primary server archive: /home/varma/kalshi_finance_expansion_2026_10_14. Compact reports/results/code synced to /Users/varmakammili/Documents/GitHub/StocksTradingTest/kalshi_finance_expansion_2026_10_14. Large raw inputs and prior-cache links remain on the new server; none were moved back to the retired server.')
add('Public references: https://kalshi.com/category/financials ; https://docs.kalshi.com/api-reference/market/get-market-candlesticks ; https://docs.kalshi.com/getting_started/fee_rounding ; https://external-api.kalshi.com/trade-api/v2 . Series-specific fee multipliers and settlement definitions are retained in inventory and source metadata.')
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
(P/'summary.json').write_text(json.dumps(dict(recommendation=['USD/CAD15m adaptive PAPER first','Dow hourly adaptive PAPER second'],comparison=summary,live_changes=False),indent=2)+'\n')
print('WROTE REPORT',len(lines),'lines')
