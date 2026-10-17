"""Render the research record from saved results, without changing any bot."""
from pathlib import Path
import json,pickle,hashlib,datetime as dt
import study as s

P=Path(__file__).resolve().parent
def read(name):return json.loads((P/name).read_text())
def money(x):return f'${x:+.2f}'

if __name__=='__main__':
    quote=read('quote_summary.json');book=read('book_summary.json');delay=read('book_delay10_summary.json')
    cap=read('capacity.json');unc=read('uncertainty.json');cal=read('calibration.json');liq=read('liquidity.json')
    second=read('pass2_quote_summary.json');audit=read('capture_audit.json');verify=read('source_verification.json')
    markets=pickle.loads((P/'features.pickle').read_bytes())
    lines=['# BTC / ETH quantitative follow-up',
      '', 'Research completed October 2, 2026 ET / October 3 UTC. Results below are historical simulations, not actual account profits or a forecast.',
      '', '## Decision',
      '', '**Prioritize measurement and a shadow evaluation of event-driven execution for the existing BTC/ETH signals.** No tested additional strategy demonstrates a robust, substantial frequency increase at acceptable cost across the examined periods. The closest additional price-rule candidate is ETH-only Gaussian-edge8, but its small incremental portfolio benefit is uncertain and partly comes from changing the timing of existing entries. Keep it in research.',
      '', 'The concrete implementation from this pass is a separate, running BTC/ETH recorder: full order-book snapshots/deltas, public trades, 1 Hz and 5 Hz CF benchmark ticks, raw timestamps, and published live decisions. It closes the missing high-frequency execution-data gap. It does not submit orders. Production entry rules, size, risk limits, arm files, ledger and dashboard are unchanged.',
      '', '## Scope and reproducibility',
      '', f'- {len(markets):,} observed settled markets across four assets; 50 days August 13–October 1 plus the supplemental October 2 UTC day. The first four hours of October 2 were in an older cache; the remaining twenty hours were fetched here. Do not call the full day an untouched holdout.',
      '- 14 predeclared strategy rules, then six separately frozen complementary-side rules, each evaluated for BTC, ETH and both. Three additional daily-budget scenarios: 63 exploratory comparisons in total. See PLAN.md, PASS2.md and PASS3.md.',
      '- Logistic models fit only August 13–27. August 28–September 11 and September 12–October 1 are reported separately. Earlier work reused these periods, so chronological separation is not a claim of a pristine holdout.',
      '- Every entry is five contracts. Baseline is the currently active z2 + z3 design across BTC/ETH/SOL/XRP, shared 20-contract / $19 exposure / $10 daily budget including open risk, $50 starting cash. Added strategy rules operate only on the indicated BTC/ETH assets.',
      '- Fees plus an extra 1 cent per contract are deducted. Alternate runs use two cents, maximum allowed entry prices, subsequent recorded books requiring all five contracts, 10-second minimum book delay, and a one-minute quote delay.',
      '- Entry features use completed input minutes only; labels and final market volume are excluded from predictors. Repeated observations from one market sum to training weight one. Basic model: market midpoint odds, cushion, time and asset. Rich model also adds recent volatility, own/cross-asset momentum, prior quote change and prior minute volume.',
      '- Baseline replay reproduces the separately validated production-engine result exactly: 302 trades, 298 wins / 4 losses, net $65.9172 for September 12–October 1.',
      '- Source hashes, input hashes, individual trades and fitted parameters are saved. Trusted locally generated pickle files hold large replay inputs/outputs; JSON summaries are readable without Python.',
      '', '## Main comparison — September 12–October 1 UTC',
      '', 'Each row adds one candidate to the two current lanes under the same shared budget. A trade is one market entry containing five contracts. Profit changes include displaced/repriced baseline entries; candidate-alone profit is not added arithmetically.',
      '', '| Design | Total trades | Wins / losses | Total net | Change in net | Drawdown |',
      '| --- | ---: | ---: | ---: | ---: | ---: |']
    b=quote['later']['baseline']
    lines.append(f"| Current z2 + z3 | {b['n']} | {b['wins']} / {b['losses']} | {money(b['net'])} | — | ${b['drawdown']:.2f} |")
    chosen=[('ETH value / modeled edge ≥8c','gaussian_edge8:ETH'),('BTC/ETH late z3','late_z3:BOTH'),('BTC/ETH late z2','late_z2:BOTH'),('BTC/ETH adaptive-volatility edge5','adaptive_edge5:BOTH'),('BTC official-index z2','official_z2:BTC'),('BTC settlement-average edge5','official_average_edge5:BTC'),('BTC/ETH richer fitted edge3','rich_edge3:BOTH')]
    for label,key in chosen:
        v=quote['later']['candidates'][key];r=v['joint']
        lines.append(f"| + {label} | {r['n']} | {r['wins']} / {r['losses']} | {money(r['net'])} | {money(v['incremental_net'])} | ${r['drawdown']:.2f} |")
    lines += ['', '### What survives a second look?', '',
      '**ETH value:** ask 55–<95c, z ≥ 1.5, 3–10 minutes, Gaussian score minus ask/fee≥8c, cap 93.9c. It adds eight net entries and $2.92 in the later period; earlier validation adds nine and $4.88. Earlier recorded books add $6.06, and delayed books add $8.05. However, its later standalone result is only 26 trades, 23 wins / 3 losses, +$1.56 with $5.15 drawdown. Within the joint run the candidate lane itself is 13 trades, 11 wins / 2 losses, **−$0.19**; improved timing of some existing market entries explains why total portfolio change is positive. This is not eight independent guaranteed-profit trades.',
      f"The later day-bootstrap 95% interval for ETH incremental P&L per day is [{money(unc['quote']['later']['gaussian_edge8:ETH']['daily_bootstrap_95'][0])}, {money(unc['quote']['later']['gaussian_edge8:ETH']['daily_bootstrap_95'][1])}]. Removing the three best incremental days makes the gain negative. The initial 15-day period was also negative. It is a paper candidate, not a validated live edge.",
      '', '**BTC official settlement reference:** fetched another 506 hours of whole-second BRTI history, including the later period and October 2. Merely replacing the proxy with the correct published strike/benchmark does not produce a new profitable signal. BTC official z2 adds 42 later-period trades but costs $4.12 relative to baseline. The final-average probability model adds 83 but costs $71.77; it also loses on October 2. Reject these frozen versions.',
      '', '**Opposite-side purchases:** the six complementary-side tests use NO ask = 1 − YES bid (and the symmetric YES calculation), with full depth and fees. The basic calibrated model finds no qualifying bargains. Rich-model and volatility-based variants generate more trading but fail later validation. The official-average contrarian BTC variant adds 91 trades and reduces net by $16.24. No tested complementary-side rule is promoted.',
      '', '## Capacity without changing a signal',
      '', '| Shared daily budget | Later trades | Later net | Earlier validation net | Earlier validation drawdown |',
      '| --- | ---: | ---: | ---: | ---: |']
    for budget in ('10','12','15','20'):
        r=cap['quote']['later'][budget];ear=cap['quote']['selection'][budget]
        lines.append(f"| ${budget} | {r['n']} | {money(r['net'])} | {money(ear['net'])} | ${ear['drawdown']:.2f} |")
    lines += ['', 'Raising the budget to $15 adds 24 trades (+7.9%) and $4.83 in the later 20 days, but costs $1.80 and increases drawdown by $6.39 in the earlier validation period. Recorded books also show worse earlier profit and drawdown. This buys capacity with higher risk; it does not improve predictive edge. A $12 budget is the smaller alternative: only five extra later trades and $1.10. Neither change is activated.',
      '', 'At zero realized daily P&L, a z2 five-contract position reserves $4.75 and z3 reserves $5. The current $10 budget initially permits two positions; a $15 budget permits three. The unchanged $19 exposure cap allows four z2 positions exactly, but four positions including a z3 exceed it. Budget blocks count repeated minute opportunities, not necessarily distinct missed trades.',
      '', '## Liquidity and actual execution',
      '', '| Asset | Later median 15-minute volume (contracts) | Earlier z2 qualifying minutes with 1-contract depth | Same minutes with 5-contract depth |',
      '| --- | ---: | ---: | ---: |']
    for a in s.old.ASSETS:
        l=liq['later'][a];d=liq['selection'][a]['depth']
        lines.append(f"| {a} | {l['median_volume']:,.0f} | {d.get('current_signal95_depth1',0)} | {d.get('current_signal95_depth5',0)} |")
    ratio=liq['later']['BTC']['median_volume']/liq['later']['ETH']['median_volume']
    lines += ['', f'BTC median volume is about {ratio:.1f}× ETH. For BTC, all 95 earlier z2 decision minutes with an executable one-contract recorded book also had five-contract depth (and twenty). Yet only 95 of 227 qualifying minute signals had a price/depth-valid first subsequent snapshot. ETH was 51 of 132, with all 51 also supporting five. This points toward price availability and timing rather than five-contract size as the primary historical BTC/ETH execution constraint. These are sampled-book observations, not a live fill-rate estimate.',
      '', 'The old book samples are roughly 30 seconds apart. The “10-second delay” experiment selects the first later recorded book within 35 seconds; it also drops some observations when no sufficiently prompt later snapshot exists. It is not a tick-exact simulation of 10 seconds of exchange latency. September uses minute quote proxies with no historical depth assurance.',
      '', 'Actual production: the nine submitted orders since September 30 UTC in the initial snapshot filled all five requested contracts; a further five-contract ETH entry filled during this audit. BTC KXBTC15M-26OCT022230-30 settled at +$0.3319 actual net. Source verification found no live-code changes (8/8 hashes) and an unchanged stable checkpoint (12/12). These few fills establish operation, not future performance.',
      '', '## Mathematical findings',
      '', 'For a $1 binary contract held to settlement, modeled expected net per contract is p − entry_price − fee − execution_cost. High probability alone is not enough: the price already incorporates much of it.',
      '', 'At 97.5c, the modeled taker fee is about 0.1706c; adding 1c execution stress requires a win probability above 98.6706%. Five contracts earn about 6.64c on a win after those costs and lose about $4.9336 on a loss. Roughly 75 such wins are needed to offset one loss. More high-price bets can therefore make the bot busier without improving its economics.',
      '', 'Gaussian cushion probabilities overstate certainty in parts of the BTC tail. In later-market decision observations with 2 ≤ z < 3, the weighted predicted loss rate is 1.15%, versus 2.60% observed. These are repeated, market-weighted observations, not independent trades. Student-t tails and short-volatility adjustments do not automatically repair the trading results.',
      '', 'The fitted model slightly improves broad BTC Brier error from 0.152859 to 0.152433 and ETH from 0.147798 to 0.147234, but that calibration gain does not survive spread/fees in a useful high-frequency trading rule. Model-score improvement and executable profit are different tests. All 60 strategy/asset screens and all 3 budget alternatives remain exploratory; uncertainty output includes the 63-comparison adjustment.',
      '', 'For the final-minute arithmetic average, a driftless Brownian approximation gives future-average variance sigma² × (minutes_remaining −2/3). This formula was tested with completed CF prices and the published strike. Its failed trading result shows why correct settlement mechanics still require empirically calibrated dynamics. No Gaussian probability is presented as guaranteed or established fair value.',
      '', '## Implemented next experiment',
      '', 'Service: `kalshi-btc-eth-research-capture.service`, under `/home/varma/kalshi_btc_eth_quant_2026_10_02`. It records both BTC and ETH; authenticates market-data subscriptions only; does not construct an order/account client. Production decisions are copied from the published status file, excluding balances and the trade ledger. It has bounded CPU/memory and stops at 500 observed quarter-hour closes, 2 GiB or 7 days, whichever comes first. The disk cap may end collection first; limits do not promise 500 full intervals.',
      '', f"Initial compressed segment audit: {audit['counts'].get('orderbook_delta',0):,} book deltas, {audit['counts'].get('trade',0):,} public trades, no sequence gaps, no negative book depth and no deltas before a snapshot. Median server-send-to-recorder delay {audit['latency']['server_send_to_capture_ms']['p50']:.0f}ms; 95th percentile {audit['latency']['server_send_to_capture_ms']['p95']:.0f}ms. These are receive-time measurements, not order execution latency or proof of an edge. Segments cut by restart are marked partial.",
      '', 'The next execution hypothesis is precise: once an existing completed-minute z2/z3 signal is valid, compare its current bounded REST rechecks with a WebSocket book trigger that reacts immediately when all five contracts are available inside the **same** limit and 25s signal-age window. Preserve spread/floor checks, one IOC per market, reconciliation and shared risk. Separately report same-signal opportunities recovered, failed/expired signals, post-trigger price movement and fees. There is currently no evidence-backed estimate of extra fills for this architecture; the recorder supplies the required data.',
      '', 'Promotion requires observed incremental executable opportunities and positive cost-stressed incremental results in a frozen forward sample, with no duplicate submissions or relaxed price/risk rules. The new recorder is active; an event-driven live order path has not been activated or claimed ready.',
      '', '## Limitations and references',
      '', '- Quote fills are assumptions; full displayed historical depth is stronger evidence but still no guarantee of live IOC fills. API outages, ambiguous orders, queue contention and all intraminute retry paths are not fully reproduced.',
      '- Drawdown is measured on settlement/close-grouped P&L, not continuous mark-to-market. Tables use UTC days; the risk budget resets in America/New_York. A UTC daily loss can exceed an ET budget without representing the same risk day. The one-minute quote-delay run deliberately exceeds the live signal lifetime and is only adverse-price sensitivity.',
      '- Day-block bootstrap conditions on the simulated portfolio path and does not simulate a new regime. The October 2 supplement is one day only. Historical failures are retained, including the initial model-fit-period trading outcomes.',
      '- Official-index history coverage is recorded separately. ETH official-index data remains limited to the old 30-day period and its boundary; later ETH official-index results must not be interpreted as full-period tests.',
      '', 'Primary documentation checked during this work: [Kalshi order-book updates](https://docs.kalshi.com/websockets/orderbook-updates), [CF benchmark feed and averaging semantics](https://docs.kalshi.com/websockets/cfbenchmarks-value), [CF 5Hz feed](https://docs.kalshi.com/websockets/cfbenchmarks-value-5hz), [authenticated benchmark history](https://docs.kalshi.com/cfbenchmarks/rest-passthrough), [official fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf). Actual published strike and outcome come from the cached Kalshi market responses.',
      '', 'Verification: ten targeted tests pass, covering causal bars, missing inputs, complementary-side ask/depth, shared budgets, training chronology, baseline parity, subscription scope, sequence gaps and excluding private account/ledger fields. Stable and production hashes match. Bot/dashboard services were healthy and the dashboard returned HTTP200 after capture started.',
      '', '## Complete later-period screen',
      '', '| Candidate / assets | Extra trades | Incremental net | Earlier quote increment | Earlier book increment |',
      '| --- | ---: | ---: | ---: | ---: |']
    for prefix,qq,bb in [('',quote,book),('pass2_',second,read('pass2_book_summary.json'))]:
        for k,v in qq['later']['candidates'].items():
            # Suppress misleading ETH official rows with only a boundary market.
            if 'official' in k and k.endswith(':ETH'):continue
            ear=qq['selection']['candidates'].get(k,{});eb=bb['selection']['candidates'].get(k,{})
            lines.append(f"| {k} | {v['incremental_trades']:+d} | {money(v['incremental_net'])} | {money(ear.get('incremental_net',0))} | {money(eb.get('incremental_net',0))} |")
    (P/'REPORT.md').write_text('\n'.join(lines)+'\n')
