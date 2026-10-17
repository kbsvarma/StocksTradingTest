from pathlib import Path
import datetime as dt,gzip,json
import numpy as np
import study as s
P=s.P
def money(x):return ('−' if x<0 else '+')+f'${abs(x):.2f}'
def main():
    results=json.loads((P/'results.json').read_text());books=json.loads((P/'book_results.json').read_text());audit=json.loads((P/'audit.json').read_text())
    lines=['# BTC every-window entries with loss control — five-pass research',
    '', '**Decision: no tested variant establishes a dependable live edge.** This study completed 16,144 specified parameter combinations, including controls and some economically equivalent cases. It found a few small positive retrospective results, but none stayed positive in all three main historical blocks with a 1c execution allowance per transaction. No strategy was activated. Existing live z2/z3 sources and the stable checkpoint are unchanged.',
    '', 'This is a finite, broad search, not every conceivable strategy. The user asked for iteration beyond simple stops: the work progressed through mechanical exits, policy-specific payoff models, variable entry timing with deadlines, a single recovery/reversal entry, and a classifier at the stop event itself. All failures and full grids are retained.',
    '', '## Direct test of the user’s hypothesis',
    '', 'Buy the BTC favorite after the first completed minute in every listed market. One contract; hold survivors to settlement. September 12–October 1 UTC has **1,896 entries / 1,896 available markets**, with **1,128 eventual winning picks and 768 losing picks: 59.49% directional accuracy**. Net includes modeled purchase/sale fees and 0.25c per actual transaction. These are quote replays, not actual bets.',
    '', '| Loss-control rule | Cash wins / losses | Cash win rate | Net | Mean cash win | Mean cash loss |', '|---|---:|---:|---:|---:|---:|']
    controls=[(0,'Hold to settlement'),(2,'Stop 5c below purchase'),(4,'Stop 10c below purchase'),(10,'Exit when bid reaches 10c'),(11,'Exit when bid reaches 15c')]
    for cid,name in controls:
        v=results[str(cid)]['periods']['later']['base'];lines.append(f"| {name} | {v['cash_wins']} / {v['cash_losses']} | {v['cash_win_rate']:.2%} | {money(v['net'])} | {v['mean_cash_win']*100:.2f}c | {v['mean_cash_loss']*100:.2f}c |")
    lines += ['', 'The 5c stop cuts average cash losses substantially, but reduces actual winning trades from 59.49% to 26.05%. Its average winner earns 36.95c and its average loser costs 17.15c. At those realized payoffs, break-even requires roughly **31.7% cash wins**. This illustrates why the original pick being correct more than half the time is insufficient after stops.',
    '', 'A nominal stop distance is not a guaranteed maximum cash loss: spread, both transaction fees, execution allowance, gaps, and observation/arrival delay all count. Minute observation misses intraminute paths; dense-book verification below separately tests faster monitoring.',
    '', '## Exactly what the exits save and sacrifice',
    '', '| Exit | Stopped eventual losers | Net cash recovered from losers | Stopped eventual winners | Payout forfeited on winners | Change versus holding identical entries |', '|---|---:|---:|---:|---:|---:|']
    for cid,name in controls[1:]:
        v=results[str(cid)]['periods']['later']['base'];lines.append(f"| {name} | {v['saved_losers']} | ${v['net_recovered_from_losers']:.2f} | {v['stopped_winners']} | ${v['net_forfeited_on_winners']:.2f} | {money(v['exit_improvement'])} |")
    lines += ['', 'Recovering money from a loser does occur. The problem is the offsetting cost of liquidating positions that would have recovered. The 15c bid trigger saves **$58.37** on 658 eventual losers but gives up **$70.10** on 78 eventual winners. Its actual average recovery on stopped losers is **8.87c**, after sale costs and observed price gaps. The 10c trigger recovers only 5.67c on average. Higher salvage thresholds were also tested in the grid; a trigger is not a promised sale price.',
    '', '![BTC stop economics](stop_economics.png)',
    '', '## Five iterations and the retained failures',
    '', '| Pass | Combinations | What changed | Frozen leader’s later-period result |', '|---|---:|---|---:|',
    '| Mechanical grid | 12,350 | 130 entry policies × 95 exits: tight/salvage/confirmed/late/time-scaled/trailing/underlying/probability exits | Best early stop family leader: −$8.29 |',
    '| Payoff model at entry | 98 | Predict cash proceeds after each stop policy; choose either side without abstention | Leading stopped version: −$42.09 |',
    '| Payoff-aware timing | 126 | Wait for estimated 0/1/2c payoff; mandatory deadline at 8/4/2 minutes | Primary −$4.39; secondary −$1.06 |',
    '| One recovery or reversal | 3,360 | At most two sequential entries; require recovery or flip, optionally confirm momentum | Best early recovery leader: −$8.19 |',
    '| Recovery-aware stop | 210 | Estimate recovery at the drawdown using current quote, z/momentum and prior path | Primary −$6.47; secondary −$5.34 |',
    '', 'The first-pass best early stop leader enters the Coinbase-directed side with 12 minutes left and activates a 20c salvage trigger only in the last minute. It barely improves exit P&L (+6.84c across the entire later period) while reducing settled drawdown, but its total remains negative. The same-entry hold control is −$8.36. Loss control can alter risk without establishing positive expectancy.',
    '', 'The fourth pass is an explicit exception to the initial one-entry research rule: at most one additional sequential bet was allowed after closing the first. Its net includes all legs, and no live ownership or one-IOC rule was changed. Its best recovery leader adds only +$0.25 from second entries over the later 20 days and still loses overall. Every recovery variant fails the three-main-block positivity check even before the 1c cost stress.',
    '', '## Closest positive research leads — selected after seeing the results',
    '', 'These are exploratory secondary cells, not the rules selected before the later-period replay. Their small apparent gains must not be presented as a discovered or validated edge.',
    '', '| Secondary case | Later entries / windows | Base net | Net at 1c per transaction | Recent 72-window dense-book net |',
    '|---|---:|---:|---:|---:|',
    '| Payoff model; two minutes left; 10c salvage | 1,843 / 1,896 | +$6.14 | −$9.30 | −$1.39 |',
    '| Payoff-aware entry deadline; 20c salvage | 1,883 / 1,896 | +$5.89 | −$9.29 | −$1.50 |',
    '| Payoff-aware deadline; confirmed 15c salvage | 1,882 / 1,896 | +$6.02 | −$8.17 | −$2.77 |',
    '', 'These cases often buy cheap underdogs. Cash win rates are approximately 22–32%, so their economics differ from the proposed >50%-winner pattern. Their date-block 90% net intervals include zero; for the first case, approximately −$13.77 to +$25.82. They also fail literal every-listed-window participation because some late selected sides have no executable quote. The 20c salvage deadline case adds only $1.96 from its stop relative to holding the same entries; most of its small base profit comes from entry selection.',
    '', '## Recorded-book check',
    '', 'Reconstructed full displayed size one from archived approximately-30-second books and 72 recent dense BTC captures. Stops use actual future bids after 250ms or 3s modeled arrival latency, never their nominal trigger price. Both minute observation and continuous observed-book monitoring are retained. Confirmation uses elapsed time, not a count of rapid quote messages.',
    '', '| First-minute favorite strategy | First historical block | Second historical block | Recent dense 72 windows |', '|---|---:|---:|---:|']
    for cid,name in controls:
        v=books[f'{cid}_continuous1_delay0.25']['periods'];lines.append(f"| {name} | {money(v['development']['base']['net'])} | {money(v['validation']['base']['net'])} | {money(v['october3']['base']['net'])} |")
    lines += ['', 'The recent sample happens to favor holding: 51 of 72 first-minute picks eventually win. The 5c stop leaves only 17 cash winners and about +$0.16 at the base allowance. Its average cash loss is 10.72c, lower than the minute proxy’s 17.15c but still materially above the nominal 5c trigger. Faster observation improves execution realism; it does not restore the stopped winners. This single short sample is not proof of an edge.',
    '', 'The old book archive supports the early 30-day period plus one boundary market only; it does **not** provide full later-period depth evidence. The late two-minute secondary model also loses on the first historical book block under 3s latency. Displayed depth remains assumed fillability, not an actual IOC fill ledger.',
    '', '## Chronology and material limitations',
    '', '- Data: 4,914 listed BTC markets, August13–October3 20:45 UTC; blocks 1,414 / 1,424 / 1,896 / 96 / 84. Calendar maintenance gaps and missing listed windows are distinct from strategy skips. Current-day results cover a partial day.',
    '- All these dates have been used in prior research. Repeated searches, adaptive passes, overlapping configurations and secondary inspection create selection bias. The full arrays retain failures; no multiple-testing-adjusted proof is claimed.',
    '- The inherited rich model was fitted on Aug13–27. The inherited market-combined official-index model was calibrated through Sep11: early scores of families using it are partly in-sample, not independent validation. All inherited coefficients predate the later Sep12 period. New policy-payoff models fit Aug13–27 only; the recovery-aware model fits those early labels but some training entry choices inherit the above combined model. These distinctions further weaken positive early evidence.',
    '- Features use completed bars only. Missing entry-model inputs fall back to the favorite. No observation low/high was used as an oracle to claim a stop fill. Signals and executions have separate prices; delayed execution can change entry and exit counts.',
    '- Fee model: current quadratic multiplier one, purchase and sale fees with direct-member $0.0001 cash rounding. Base allowance is 0.25c **per transaction**; 1c/2c stress replaces it. Historical fee-policy changes, rebates/incentives, partial IOC fills and queue effects are not reconstructed.',
    '- Results are standalone one-contract experiments. They are not additive to live-bot profit and do not simulate shared ownership, shared daily budget, or account cash constraints. A tight stop or a second sequential entry does not cap gap losses or total market loss.',
    '', '## Verification and saved work',
    '', f"Twenty-three targeted tests pass. An independent Decimal audit reconciles **{audit['single_entry_rows']:,} single-entry rows and {audit['recovery_rows']:,} bounded-recovery rows across {audit['ledger_files']} stored ledgers**. It verifies cash, actual outcome, chronology, pre-expiration sales, two-entry limits and the saved-loss/forfeited-win identity. Vectorized model features match 19,656 scalar feature rows. Tests exercise adverse gaps, order latency, confirmation timing, unavailable bids, causal features, missing-model fallback, and mandatory deadlines.",
    '', 'Eight production source hashes and twelve stable checkpoint hashes match. Bot and dashboard were active in LIVE mode, with a fresh heartbeat and no reported halt/error. No live orders, funding transfers, sizing changes, activation, or new background service were performed.',
    '', 'All code, input hashes, compact input arrays/books, frozen selections, fitted coefficients, full screen matrices and individual finalist ledgers are saved in this directory and mirrored to `/home/varma/kalshi_btc_every_window_stops_2026_10_03`. The source archives remain in their earlier research directories on the new server.',
    '', 'For reproduction, use Python 3.12 with NumPy and Numba. Keep the saved frozen files intact; work in a copied directory when rerunning selection/fitting. `study.py evaluate`, `book_replay.py`, `path_model.py evaluate`, `deadline_model.py evaluate`, `recovery.py evaluate`, `recovery_classifier.py evaluate`, `additional_books.py`, and `audit.py` reproduce the saved result families from the bundled arrays. Run the unittest suite in this folder. `prepare` additionally requires sibling raw-source studies documented in the manifests.',
    '', 'Official references used for mechanics: [Kalshi fee and cash rounding](https://docs.kalshi.com/getting_started/fee_rounding), [crypto settlement methodology](https://help.kalshi.com/en/articles/13823838-crypto-markets). Current series fee metadata is retained under `inputs/fee_metadata.json`.',
    '', '**Recommendation:** preserve these findings and keep this family in research. The positive historical cells are too small, too sensitive to cost and too selected to justify a new live every-window bot. The next credible improvement must increase the value of the selected entry or distinguish temporary drawdowns from continuing losses more accurately than the information already in the quote. More stopping alone has not accomplished that in these tests.', '']
    (P/'REPORT.md').write_text('\n'.join(lines))
    # Standalone scientific figure; no live dashboard changes.
    import matplotlib;matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),layout='constrained');colors=['#145da0','#d1495b','#6b4c9a']
    for (cid,label),color in zip([(0,'Hold'),(2,'5c loss stop'),(11,'15c salvage')],colors):
        rows=json.load(gzip.open(P/f'trades_{cid}.json.gz','rt'));rows=sorted([r for r in rows if r['cohort']=='later'],key=lambda r:r['exit_ts']);pnl=np.cumsum([r['pnl']-.0025*r['legs'] for r in rows]);dates=[dt.datetime.fromtimestamp(r['exit_ts'],dt.timezone.utc) for r in rows];axes[0].plot(dates,pnl,label=label,color=color,linewidth=1.4)
    axes[0].axhline(0,color='#777',lw=.6);axes[0].set_title('Same BTC entries: cumulative net');axes[0].set_ylabel('USD, one contract');axes[0].tick_params(axis='x',rotation=25);axes[0].legend(frameon=False)
    ids=[2,4,10,11];saved=[results[str(i)]['periods']['later']['base']['net_recovered_from_losers'] for i in ids];lost=[results[str(i)]['periods']['later']['base']['net_forfeited_on_winners'] for i in ids];pos=np.arange(4);axes[1].bar(pos-.18,saved,.36,label='Recovered from eventual losers',color='#278569');axes[1].bar(pos+.18,lost,.36,label='Forfeited on eventual winners',color='#d1495b');axes[1].set_xticks(pos,['5c loss','10c loss','10c salvage','15c salvage']);axes[1].set_ylabel('USD across all entries');axes[1].set_title('What stopping saves versus gives up');axes[1].legend(frameon=False,fontsize=8)
    fig.suptitle('BTC · Sep 12–Oct 1 · fees + 0.25c per transaction · quote replay',fontsize=12);fig.savefig(P/'stop_economics.png',dpi=160);plt.close(fig)
    print('Report and figure saved',flush=True)
if __name__=='__main__':main()
