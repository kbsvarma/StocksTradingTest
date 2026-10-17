"""Build reviewable text/specification from audited research outputs."""
from common import *

r=read(P/'results.json');e=read(P/'extended_results.json');d=read(P/'diagnostics.json');audit=read(P/'audit_results.json')
profile='original_favorite/very_late/'
def row(name,x):return f"{name:30s} {x['trades']:5d} {x['wins']:4d}/{x['losses']:<3d} {x['net']:+10.4f} {x['drawdown']:9.4f}"
lines=[
'KALSHI LATE-GAME FAVORITES — RESEARCH, OCTOBER 10, 2026',
'',
'Decision: advance MLB pregame favorites still leading in innings 8–9 to',
'prospective execution validation. Prefer the fixed 95–<98c variant for that',
'comparison because both historical segments were positive; keep 90–<95c as',
'a separate comparator. This is a candidate, not a demonstrated live edge.',
'Do not launch a general buy-late-favorites bot: those broad variants lose.',
'No orders, transfers, production changes, new daemon or automation occurred.',
'Existing crypto/commodity bots and Sep28 stable checkpoint were not edited.',
'',
'USER IDEA AND THE TEST',
'A pregame favorite can remain mispriced while still leading late in a game.',
'Measure late with timestamped actual score and period/inning, never minutes',
'subtracted retrospectively from the final game-end timestamp. Twelve fixed',
'profiles per league compare any leader, a comfortable leader, and the same',
'pregame favorite, at two game-clock stages and two price bands. Five source/',
'execution/cost scenarios are retained, including negative results.',
'',
'MLB RULE',
'Pregame YES midpoint >=60c, spread <=2c, measured 30 minutes before scheduled',
'start. That SAME team must still lead in regulation inning 8 or 9. Decision',
'ask 90–<95c or independently 95–<98c; spread <=2c; prior 10-minute recorded',
'volume >=5 contracts. Source delay 30 seconds, latest play age <=180 seconds.',
'Decision on a completed minute; next-minute opening quote used as a +3-second',
'execution proxy. Recheck score, game status, and cap: min(signal ask +1c,98c).',
'Execution price is worse of signal ask and next quote, plus separate 1c per',
'contract friction. One entry per game; equivalent mirror YES/NO is counted',
'once. Hold official settlement. No fitted loss exclusions or stop-loss.',
'',
'All numbers use a standalone $100 bankroll, five contracts per entry, max',
'five positions/25 contracts, $25 reserved debit including fees/friction, $20',
'daily realized-loss plus open-risk gate, $2 cash floor. Current bots do NOT',
'share this simulated budget. Fees use historical series changes: MLB 1.0',
'until Aug7 04:59:45Z, then 0.5; 0.07*M*q*p*(1-p), rounded up to $0.0001.',
'',
'EXTENDED MLB: usable replay July29–October9, 2026',
'Requested raw window July1–Oct9. July1–28 lacks usable timestamped play data;',
'those games are NOT counted as evidence for zero trades or no losses.',
'Band/scenario                  Trades  W/L       Net $  Settled DD $',
]
for band in ['p90','p95']:
 for scenario in ['base','cost2c','delay60','source90','both_delays']:
  lines.append(row(band+'/'+scenario,e['full/'+profile+band+'/'+scenario]))
lines += [
'',
'base =30-second source lag/+1c friction; cost2c =+2c friction; delay60 =',
'execute next-minute close; source90 =90-second source lag; both_delays =',
'90-second source lag and next-minute close. Each scenario reruns selection',
'and portfolio, so improved delayed results do not mean delayed identical',
'fills are better. The no-loss delayed p90 path is not the chosen strategy.',
'',
'Earlier and later segments, split by ENTRY time at Aug15 00:00 ET:',
'Band/period                    Trades  W/L       Net $  Settled DD $',
]
for band in ['p90','p95']:
 for period in ['earlier','recent']:
  lines.append(row(band+'/'+period,e[period+'/'+profile+band+'/base']))
lines += [
'The extension was selected after the recent MLB result was visible. It is',
'retrospective confirmation, not an untouched holdout. Recent extended MLB',
'includes an Aug14 scheduled game entered after midnight Aug15; this explains',
'63 versus 62 p90 entries and 67 versus 66 p95 entries in the six-league table.',
'',
'MONTHLY MLB, base (independent portfolios; do not add columns)',
'Month             p90 trades / net        p95 trades / net',
]
for month in sorted(d['price_bands']['p90']['monthly']):
 a=d['price_bands']['p90']['monthly'][month];b=d['price_bands']['p95']['monthly'][month]
 lines.append(f"{month}             {a['trades']:3d} / {a['net']:+8.4f}        {b['trades']:3d} / {b['net']:+8.4f}")
lines += [
'',
f"The two base portfolios overlap in {d['price_band_overlap_games']} games; their profits/trades cannot be",
'summed into a $100 combined bot. No combined 90–98c or parallel allocation',
'has been tested. Each band produced only ONE October entry (Oct3); this is',
'not an answer to increasing everyday trade frequency now.',
'',
'DRAWDOWN AND UNCERTAINTY',
f"p90: settled P&L drawdown ${e['full/'+profile+'p90/base']['drawdown']:.2f}; sampled bid-marked drawdown ${d['price_bands']['p90']['bid_marked_drawdown']:.2f}.",
f"p95: settled P&L drawdown $0 because all won; sampled bid-marked drawdown ${d['price_bands']['p95']['bid_marked_drawdown']:.2f}.",
'Zero settled drawdown is NOT zero intratrade risk. Bid marks omit stale/missing',
f"observations: p90 {d['price_bands']['p90']['missing_or_stale_held_marks']}, p95 {d['price_bands']['p95']['missing_or_stale_held_marks']}; maximum held quote ages 480/960 seconds.",
'These are discrete quote marks, not continuous/executable liquidation values;',
'exit fees and intraminute extremes are not included in marked drawdown.',
'',
'p90 observed 96.39% wins against 93.18% mean all-in break-even; Wilson 95%',
'interval 89.90–98.76%. Three-day block net interval about -$11.84 to +$31.09.',
'Neither corrects selection or proves a future edge. p95 all 88 wins has a',
'95% Wilson lower win bound of 95.82%, BELOW its 97.11% mean break-even.',
'Its mean win is only $0.1447 per five-contract trade; a ~$4.9 loss erases',
'about 34 such wins. An all-win sample bootstrap cannot estimate unseen losses.',
'p90 keeps +$11.71 after removing its best day, but its earlier segment lost',
'$3.43. No losing trade was removed based on its outcome.',
'',
'ALL THREE p90 LOSSES',
]
for loss in d['losses']:
 lines.append(f"{loss['event']}: {loss['side'].upper()} {loss['ticker']}, entry {loss['entry']:.2f}, net {loss['net']:+.4f}; inning {loss['period']} {loss['inning_half']}, home {loss['home_score']} away {loss['away_score']}, {loss['outs']} outs.")
lines += [
'',
'SIX-LEAGUE COMPARISON, scheduled games Aug15–Oct9',
'Same pregame-favorite/very-late rule, independent $100 portfolios.',
'Base includes fees +1c friction. NFL/NCAAF last5min Q4; NBA/WNBA last2min Q4;',
'NHL last5min period3; MLB innings8–9. Overtime excluded.',
'League/band                    Trades  W/L       Net $  Settled DD $',
]
for s in ['KXMLBGAME','KXNFLGAME','KXNCAAFGAME','KXNBAGAME','KXWNBAGAME','KXNHLGAME']:
 for band in ['p90','p95']:lines.append(row(s+'/'+band,r[s+'/'+profile+band+'/base']))
lines += [
'NFL and NHL are small-sample secondary leads, not credible win-rate estimates.',
'NBA evidence is preseason and insufficient; college football is negative.',
'WNBA p90 loses; its eight p95 wins are too few to recommend. NHL uses ESPN',
'wall-clock fallback; revised historical feed timestamps are not proof of',
'first receipt or actual live source latency.',
'',
'COMMON $100 CROSS-LEAGUE PORTFOLIO, base',
'Profile                        Trades  W/L       Net $  Settled DD $',
]
for family in ['leader','comfortable','original_favorite']:
 for clock in ['late','very_late']:
  for band in ['p90','p95']:
   key=f'{family}/{clock}/{band}';lines.append(row(key,r['ALL/'+key+'/base']))
lines += [
'Cross-league original-favorite/very-late/p90 returns +$12.09, but combined',
'source+execution-delay stress has a negative later segment (-$3.75). That',
'supports separating league research, not launching the whole sports basket.',
'',
'COVERAGE, DATA REPAIRS, AND WIDER MARKET WORK',
'The six-league archive catalogued 1,862 games. A quote-only screen selected',
'1,476 potential 90–98c games for play data: MLB591/NFL97/NCAAF571/NBA23/',
'WNBA89/NHL105. Usable score states: 591/97/292/12/81/105 respectively.',
'Earlier MLB added559 games,228 with usable play states,331 without. Total',
'MLB play coverage819 games. Coverage exclusions are visible, not silently',
'treated as traded or losing games. Pregame midpoint<60c is a design rejection,',
'not automatically a data gap. Timestamp/score/identity availability restricts',
'the sample and may create coverage bias.',
'',
'Two MLB archives had truncated quotes. Fresh official candles were merged',
'and EVERY portfolio rerun. Original inputs/results remain in before_quote_recheck.',
'Some p95 leader/comfortable controls changed by one entry; selected original-',
'favorite p90/p95 results did not change. Event/period final aggregate scores',
'are not used as earlier observations; only individual timestamped play states.',
'',
'EPL/MLS/LaLiga catalogs were also inspected (50/138/71 games). Sampled Kalshi',
'soccer play tapes lack wall-clock timestamps. ESPN commentary contains wall',
'time but not contemporaneous cumulative scores on each play; a causal goal/',
'VAR-score reconstruction is still needed. There is NO new soccer backtest',
'result. Three-way soccer NO is never treated as a single opposing-team YES.',
'',
'Prior wider-market census/replays were read, not relabeled as new full-market',
'backtests. Tennis prior serving-for-match wins remain historical and feed/',
'fill-unverified; the previous sports implementation also mismatched league',
'scope and persistence rules and double-counted mirror contracts. This study',
'uses exact target IDs, one game owner and actual score/time. Previously',
'shelved weather and large-capital carry proposals remain shelved. New $100',
'perpetual research remains in kalshi_perp_smallcap_expansion_2026_10_10.',
'',
'WHAT MUST BE VERIFIED BEFORE LIVE',
'1. Prospective source arrival: persist receive timestamps and revisions;',
'   test game/score/inning/terminal updates and postponement handling.',
'2. Actual five-contract order-book depth at qualifying prices. Prior traded',
'   volume and minute opening quotes do NOT establish available fills.',
'3. Current fee engine, exact YES/NO settlement rules, partial fills, duplicate',
'   event ownership, restart/reconciliation and stale-feed shutdown behavior.',
'4. New independent outcomes with the frozen rule. Review net after real costs',
'   and all losses; do not repeatedly tune thresholds on this same archive.',
'5. Current season frequency and common budget interactions if it shares',
'   capital with existing bots. Standalone returns are not incremental P&L.',
'',
'The bounded current public snapshot found five open MLB events, including',
'future games; it did not validate a qualifying live fill. No continuous',
'collector or sports trading service was installed.',
'',
'VALIDATION AND REPRODUCTION',
f"Audit: {audit['counts']['assertions']:,} assertions across {audit['counts']['scenarios']} repeated scenarios, {audit['counts']['ledger_rows']:,}",
f"ledger records and {audit['counts']['unique_decisions_checked']:,} distinct decisions; zero errors. Repeated scenarios",
'are not independent trades. Six causal tests passed (future result/score',
'isolation, decision-before-execution choice, feed lag/terminal, fee boundary).',
'Audits verify arithmetic and chronology, not feed correctness or live fills.',
'Historical provider event times, corrected plays and scheduled-start metadata',
'are not complete as-of first-received versions. Delay stresses reduce but do',
'not eliminate that uncertainty; prospective timestamp capture remains needed.',
'',
'On primary server: cd /home/varma/kalshi_late_favorites_2026_10_10',
'Use /home/varma/kalshi/venv/bin/python for late.py, analyze_extension.py,',
'audit.py, diagnostics.py and -m unittest -v test_causality. write_report.py',
'regenerates this report, APPENDIX and research-only specification. Collectors',
'use public GETs; rerunning collection is not needed for offline reproduction.',
'Raw data/receipts, source snapshots and pre-repair files are archived on that',
'server. Local folder holds code, compact outputs, full ledgers and manifest.',
'',
'PRIMARY SOURCES',
'https://docs.kalshi.com/api-reference/live-data/get-game-stats',
'https://docs.kalshi.com/api-reference/live-data/get-live-data',
'https://kalshi.com/docs/kalshi-fee-schedule.pdf',
'https://external-api.kalshi.com/trade-api/v2/series/fee_changes',
'Public Kalshi markets, milestones, candlesticks and game_stats URLs/retrieval',
'timestamps are retained in raw/*.json.gz. ESPN NHL scoreboard/summary source',
'URLs and retrieval times are in external/ and nhl_fallback_receipt.json.',
]
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
appendix=['ALL FROZEN SCENARIOS; EACH ROW IS AN INDEPENDENT PORTFOLIO','Net and drawdown in dollars; DD is settled P&L, not marked equity.','Overlapping scenarios are not independent samples.','']
for title,obj in [('SIX-LEAGUE ORIGINAL WINDOW',r),('MLB RETROSPECTIVE EXTENSION',e)]:
 appendix.extend([title,'Key | trades | wins | losses | net | settled DD | +weeks/active | unconstrained net'])
 for k,v in sorted(obj.items()):appendix.append(f"{k} | {v['trades']} | {v['wins']} | {v['losses']} | {v['net']:+.5f} | {v['drawdown']:.5f} | {v['positive_weeks']}/{v['active_weeks']} | {v['unconstrained']['net']:+.5f}")
 appendix.append('')
(P/'APPENDIX.txt').write_text('\n'.join(appendix)+'\n')
save(P/'research_spec.json',{'mode':'RESEARCH_ONLY','live_orders_enabled':False,'series':'KXMLBGAME','preferred_forward_candidate':'original_favorite/very_late/p95','comparator':'original_favorite/very_late/p90','capital':100,'contracts':5,'max_contracts':25,'max_reserved_debit':25,'daily_realized_loss_plus_open_risk':20,'cash_floor':2,'pregame_midpoint_min':.60,'pregame_minutes_before_scheduled_start':30,'pregame_spread_max':.02,'allowed_innings':[8,9],'must_lead':True,'allow_extra_innings':False,'price_bands':{'p90':[.90,.95],'p95':[.95,.98]},'price_upper_exclusive':True,'spread_max':.02,'prior_10m_volume_min':5,'source_delay_seconds':30,'source_max_age_seconds':180,'execution_cap':'min(signal ask+.01,.98)','one_entry_per_game':True,'hold_to_settlement':True,'friction_per_contract':.01,'live_fill_verification':False,'prospective_validation_complete':False,'do_not_add_independent_band_results':True,'research_selection':'Both price bands predetermined; MLB/preferred band selected after reviewing historical outcomes. No untouched holdout.'})
print('Wrote REPORT.txt, APPENDIX.txt, research_spec.json')
