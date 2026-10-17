"""Render numerical results without selecting or retuning any rule."""
import csv,json
from research import ROOT

def fmt(x):return '—' if x is None else f'{x:+.2f}'

def main():
    results=json.loads((ROOT/'research_results.json').read_text())
    rows=list(csv.DictReader((ROOT/'comparison.csv').open()))
    books=json.loads((ROOT/'book_audit.json').read_text())
    index={(r['rule'],r['split']):r for r in rows}
    shortlist=results['shortlist']
    passed=[r['rule'] for r in shortlist if r['passes_statistical_gate']]
    lines=['# Fresh Kalshi crypto revalidation — September 12, 2026','',
           f"Tested **{results['rules']} frozen rule variants** across **{sum(results['markets'].values()):,} settled markets**, four assets, August 13–September 11 UTC.",'',
           f"**{len(shortlist)} candidates** qualified using discovery and validation. **{len(passed)} passed the predeclared final-period statistical gate.** This gate alone is not permission or sufficient proof for live trading.",'',
           'No production settings, halt files, orders or trading services were changed. Research pulls used GET endpoints and a separate server directory.','',
           '## Holdout design and cost assumptions','',
           '- Discovery: August 13–27. Validation: August 28–September 3. Untouched final test: September 4–11.',
           '- One candidate per family selected using the minimum discovery/validation net EV, requiring both positive after 1c stress and minimum 100/30 trades. Final results did not enter selection.',
           '- Every entry uses the next minute’s opening ask, bounded by the signal ask plus 1c. Stop/target signals use completed-candle bids and execute at the following opening bid. Both entry and exit taker fees apply.',
           '- Fees use 7% × price × (1−price), rounded up to $0.0001 per one-contract execution. Additional stress is 1c per trade; 0 and 0.5c results are also retained.',
           '- Full-minute extra entry delay, day-block bootstrap with correction for the shortlisted families, minimum 100 final trades, and a largest-day concentration check are required.',
           '- Coinbase uses completed minute bars and an available pre-open price proxy. Benchmark uses completed whole-second index observations and the actual pre-open 60-second average. Final published strikes do not enter signals.',
           '- Two probability models use discovery-only ridge logistic fits at seven minutes remaining. Their fixed 3c model-edge threshold was not optimized on later data.','',
           '## Frozen shortlist: final test','',
           '| Rule | Trades | EV after 1c (c/ct) | P&L at 1ct | Drawdown | Extra-minute EV | Adjusted lower bound | Gate |',
           '|---|---:|---:|---:|---:|---:|---:|---|']
    # Keep the reviewed interpretation reproducible alongside numerical tables.
    conclusion=ROOT/'CONCLUSION.md'
    if conclusion.exists():lines[2:2]=conclusion.read_text().splitlines()+['']
    for r in shortlist:
        m=r['test'];b=r['adjusted_day_bootstrap'] or {};d=r['delay']
        lines.append(f"| {r['rule']} | {m['n']} | {fmt(m['ev_c'])} | ${m['pnl']:+.2f} | ${m['maxdd']:.2f} | {fmt(d['ev_c'])} | {fmt(b.get('lower_c'))} | {'Pass' if r['passes_statistical_gate'] else 'Fail'} |")
    if not shortlist:lines.append('| No qualifying candidate | — | — | — | — | — | — | Fail |')
    lines+=['','## Recorded-book execution audit','',
            'The shared bot captured 315,432 book snapshots covering all 11,352 markets. The audit covers the shortlist plus three predeclared reference rules. It uses the first snapshot 0.3–35 seconds after each candle signal, requires one contract of displayed depth within the original limit, and reprices exits after their triggers. Missing snapshots or exit depth are not filled synthetically. These are conditional, paired observations, not guaranteed historical fills or an independent sample.','',
            '| Rule | Test signals | Snapshot coverage | Complete replays | Book EV after 1c | Book P&L | Adjusted lower bound |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for r in books:
        if r['split']!='test':continue
        m=r['after_1c'];b=r['adjusted_day_bootstrap'] or {}
        coverage=r['snapshots_available']/r['candle_trades'] if r['candle_trades'] else 0
        lines.append(f"| {r['rule']} | {r['candle_trades']} | {coverage:.1%} | {r['complete_replays']} | {fmt(m['ev_c'])} | ${m['pnl']:+.2f} | {fmt(b.get('lower_c'))} |")
    lines+=['','## All tested rules','',
            'The final-test column is diagnostic for rules outside the shortlist. A good number there does not make an unselected rule a validated candidate. All EVs below include fees and 1c additional cost per trade. Maker rows are optimistic scenarios and were excluded from selection.','',
            '| Rule | Discovery EV (c) | Validation EV (c) | Final EV (c) | Final n |',
            '|---|---:|---:|---:|---:|']
    for name in dict.fromkeys(r['rule'] for r in rows):
        vals=[index[(name,sp)] for sp in ('discovery','validation','test')]
        ev=[fmt(float(v['ev_c'])) if v['ev_c'] else '—' for v in vals]
        lines.append(f"| {name} | {' | '.join(ev)} | {vals[2]['n']} |")
    lines+=['','## Limits and reproducibility','',
            '- Eight final-test days are a short regime sample; day bootstrap is approximate, especially for sparse rules. Passing would justify a forward trial, not an earnings promise.',
            '- Candle opens and periodic book snapshots do not reconstruct queue position, network latency, continuous repricing, or every subsecond adverse move. The deployed GTC/fallback policy is not certified by these tests.',
            '- This study covers minute-based crypto strategy families. It does not claim an exhaustive search of all possible strategies, nor a new validation of second-level final-average arbitrage, neutral market-making, perpetual hedges, or unrelated weather/sports/metals bots.',
            '- The API lists 2,838 markets per asset; all have candles and no duplicate tickers. The missing intervals relative to a continuous schedule occur in shared weekly windows; they were not invented or filled forward.',
            '- Exact inputs and SHA-256 hashes are retained in `fresh_data/` and `research_results.json`. Candidate definitions are in `frozen_rules.json`, the pre-results plan in `RESEARCH_PLAN.md`, and model coefficients in `fitted_models.json`.',
            '- Reproduce: `python3 -m unittest discover -s . -p test_research.py -q`; then `python3 research.py`, `python3 book_audit.py`, and `python3 render_research_report.py` from this directory. Requires NumPy.',
            '- The report’s statistical calculations operate on simulated one-contract trades. The historical bot-ledger losses are documented separately in `DIAGNOSIS.md`.', '',
            'API references: [historical routing](https://docs.kalshi.com/getting_started/historical_data), [batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks), [CF Benchmarks feed](https://docs.kalshi.com/websockets/cfbenchmarks-value-5hz), [fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).','']
    (ROOT/'RESEARCH_REPORT.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
