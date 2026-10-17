from core import *

def show(v):
 return f"{v['trades']}/{v['windows']} entries ({100*v['participation']:.1f}%) | {v['wins']}W/{v['losses']}L/{v['scalar']} other payouts | net ${v['net']:+.4f} | settled DD ${v['drawdown']:.4f}"
def main():
    out=['MARKET-WIDE EVERY-WINDOW RESEARCH — completed October 11, 2026','',
         'Research only. Current live bots and stable checkpoints remain unchanged.',
         'All dollar amounts below start from a separate $100 account, after stated fees and friction. These are hypothetical candle-based fills, not realized profits.',
         '', 'DECISION',
         'No tested high-frequency design qualifies for new live deployment. The best operational research direction is passive-first entry on the recurring daily gas/temperature pair, but its positive full-sample proxy fails the older-period test and assumes unverified fills. Do not market it as an established edge. Do not deploy blanket every-game favorites, NFL/NHL forced entries, or a five-contract version merely because the latest subset won.',
         'Rank by next research priority: 1) improve entry price and verify actual fills on the daily pair; 2) probability/execution work on liquid financial windows, currently failed on costs/delay; 3) sports every-game entries, currently negative over broad/extended history. This ranking is research value, not a recommendation to fund any of them.',
         '', 'SCOPE',
         'Fully paginated snapshot: 14,946 series definitions; 14,120 open events; 133,139 nested market records. These are not 133,139 independent backtests.',
         'New replay families: 21 game/match series selected (20 matched); NFL quarters and NHL periods; 12 noncrypto15minute assets; financial/index recurring expiries; three hourly-temperature cities; eight daily-temperature cities; eight daily-gas series; rainfall city contracts; eight full-game spread/total series.',
         'Initial matched inventory: 8,085 games, 476 quarters/periods, 18,002 financial/15minute/hourly-temperature windows (including unavailable-input windows), and 2,068 daily weather/gas windows. Followups extend CS2, NFL, WNBA, US gas and NY temperature history. These overlap and must not be summed as independent observations.',
         'Not comprehensively replayed: individual player props, mention contracts, all long-duration politics/elections/economics/entertainment outcomes. Existing crypto/perpetual results were reviewed, not presented as newly tested. Detailed omissions and every mechanical skip are retained.',
         '', 'FROZEN DAILY FINALISTS AND SHARED $100 REPLAY',
         'US gas: every daily event, previous-day23:00 ET, closest-to50c strike and favored side. NY daily high: every daily event12:00 ET, highest quoted YES probability bin. One accepted entry per event; only mechanical quote/activity/risk skips.',
         'These two rules and WNBA pre30 calibrated were selected from the shorter study, then extended unchanged to May1–Oct9 through BOTH recent and archived endpoints. The extended WNBA rolling fit has earlier calibration observations; it is the same learning rule, not the same startup state. Only the frozen daily checkpoints have complete archive collection.', '']
    j=read(P/'joint_results.json');diag=read(P/'joint_diagnostics.json')
    for name in ['US_gas','NY_high','WNBA','Daily_pair','Pair_plus_WNBA']:
        out.append(name)
        for part in ['all','older','recent','october']:
            k=f'{name}/base/{part}/q1/cost1';v=j[k];out.append('  '+part+': '+show(v)+f" | {v.get('calendar_trades_per_day',0):.2f} entries/calendar day | {v.get('zero_entry_days',0)} zero-entry days")
        for mode,q,cost in [('base',1,2),('delay60',1,1),('delay60',1,2),('base',2,1),('base',5,1)]:
            k=f'{name}/{mode}/all/q{q}/cost{cost}';out.append(f'  {mode}, {q}ct, {cost}c friction: '+show(j[k]))
        d=diag[name+'/base/all/q1/cost1'];out.append('  Selected-rule descriptive 7day-block95% interval for total net: '+str([round(x,3) for x in d['descriptive_7day_block_total_net_95pct']])+'. NOT selection-adjusted; not proof of an edge.')
        out.append('  Monthly one-contract nets: '+str({k:round(v['net'],4) for k,v in d['monthly'].items()}));out.append('')
    out+=['BROAD FREQUENCY COMPARISONS — ONE CONTRACT, 1c friction plus fees','']
    selected=[('pregame','KXMLBGAME/pre5/favorite/base/all/q1/cost1','MLB pregame favorite, Aug15–Oct9'),('followup','NFL/pre5/base/all/q1/cost1','NFL NO favorite, prior and current regular/postseason'),('followup','CS2/arrival/base/all/q1/cost1','CS2 first executable favorite within−5/+10min, Aug15–Oct9'),('segment','NFL/early/model/base/all/q1','NFL every-quarter probability model, Sep9–Oct8'),('hourly_fifteen_weather','15M_GBPUSD/m10/model/base/all/q1/cost1','GBPUSD every15min model at10min remaining'),('hourly_fifteen_weather','KXTEMPCHIHS/m30/available_favorite/base/all/q1/cost1','Chicago hourly available-contract favorite, Sep19–Oct9'),('hourly_fifteen_weather','KXNASDAQ100U/m5/available_model/base/all/q1/cost1','Nasdaq hourly available-contract model, Aug1–Oct9')]
    for stem,key,label in selected:out.append(label+': '+show(read(P/(stem+'_results.json'))[key]))
    out+=['', 'Robustness failures:',
          '- CS2 pre5 favorite: recent +8.5221 from158 trades, earlier−12.5279 from492; total−4.0058. Expanding arrival availability adds frequency but loses37.0049 over1,784 trades. Do not sell the short recent winner as a robust edge.',
          '- NFL pregame NO-favorite initial104-game sample included preseason and returned+6.6658. Proper regular/postseason extension348 windows/347 entries returned−4.1360; current65 regular games only+0.7877. Preseason mix changes the story.',
          '- NFL quarter model:227/260,+2.1097; another1c friction−0.1603;60s execution−0.8984;90s source−2.3647. October65entries−1.7077. High participation, no robust surplus.',
          '- GBPUSD15minute:564 entries,+4.9228; extra1c−0.7172;60s delay360entries−23.6175. Reject.',
          '- MLB610 entries,+2.8580 becomes−3.2420 with another1c cost. Reject.',
          '- Chicago hourly available favorite316/495,+4.9729; extra1c+1.8129 but60s delay252entries−0.5781, later split−1.2501. Base coverage only63.8%; LA counterpart−9.2271. Does not solve every-window diversified edge.',
          '- Nasdaq available model228/336,+2.0853; later−0.6075,extra1c−0.1947,executiondelay−8.9903. Reject.',
          '- NHL period books at fixed checkpoints are mostly absent/wide/inactive. Late favorite4/216 entries,+0.1466 is not a high-frequency strategy. Do not confuse it with the earlier selective late-period study.',
          '', 'FULL-GAME SPREAD/TOTAL RESULTS — ALL TESTED PROFILES, ONE CONTRACT']
    lines=read(P/'lines_results.json')
    for k,v in sorted(lines.items()):
        if '/base/all/q1/cost1' in k:out.append(k+': '+show(v))
    out+=['','UNIFORM SHARED-BANKROLL CONTROLS']
    for k,v in read(P/'pooled_results.json').items():
        if '/base/all/q1/cost1' in k:out.append(k+': '+show(v)+' | capital blocks '+str(v['blocked']))
    out+=['','PASSIVE-FIRST OPERATIONAL SENSITIVITY — UNVERIFIED FILLS',
          'Same daily pair and entry side/time; whole-cent bid1c below initial ask, ignore first minute, require a later traded price at least1c strictly through the order and minute volume>=5. Cancel at5min, optionally take the same side within originalask+2c/99c. Makerfee0 is a current-fee hypothesis;1/2c extra friction is still charged. This cannot establish order acknowledgement, continuous book presence, queue or actual fills.',
          'The frozen1c-through fallback produces241/324 entries,160W/81L, net+3.3470 at1contract, DD10.7624;180 of those entries are UNVERIFIED passive-fill proxies. Another1c friction leaves+0.9370. Older May1–Aug14 loses6.1856; recent Aug15–Oct9 gains9.5326. Five-contract projection+16.7472 has53.8080 settled DD, so positive headline profit is not sufficient.',
          'Requiring2c trade-through gives232 entries/+4.5203, but older−4.2933. Passive-only1/2c variants also do not establish an independent edge. Do not quote these as confirmed profitable fills. This tests adverse selection more honestly than adding a hypothetical penny saving to every original trade.',
          '', 'WHAT THE TEST DOES AND DOES NOT ESTABLISH',
          'Every-window means a compulsory decision, not a guarantee that every venue quote can be filled. Skips are enumerated for unavailable schedule/quote, spread/activity, execution limit, stale in-game source, and capital. More fills at a negative edge compounds losses.',
          'One-contract baseline separates signal economics from accidental capacity selection. Five contracts are replayed chronologically; profits cannot be multiplied by5. Reported drawdown uses settled outcomes, not intratrade bid marks.',
          'Base: spread<=5c, last10minutevolume>=5, ask1–99c, adverse next-minute opening-ask proxy, cap signal+2c/99c. Costs include quadratic taker fee and1c/contract, plus2c stress. Direct-member/current normalized fee assumptions; actual historical fees may differ.',
          'Risk: $100,25contracts,$25 reserved debit,$20 daily realized+open-loss allowance,$2cash floor. Actual settlement cash release. All period slices reset independently and need not add to constrained full runs.',
          'Point-in-time source receipt, original schedule revisions, executable depth and queue/latency remain unverified by candles. Large search and selected followups are not a fresh prospective holdout. No raw positive cell alone qualifies for deployment.',
          'Prior BTC every-window study:1,896 first-minute entries,1,128wins/768losses (59.5%),−$46.35 at its different0.25c cost assumption. Stops did not repair it. This is old evidence and is not directly pooled with the new1c-cost results.',
          '', 'VALIDATION AND RECORDS']
    for file in ['audit.json','raw_audit.json','passive_audit.json']:
        a=read(P/file);out.append(file+': '+str(a))
    out+=['Nine accounting/causality unit tests passed. Audit row/assertion counts include repeated scenarios and are not independent trade counts.','METHODS.txt explains the fixed grid, boundaries and caveats. APPENDIX.txt lists every baseline profile. Full results, ledgers, quote payloads and skips remain on the new server; local report/results copies are provided. See REPRODUCE.txt for exact commands and research dependencies.','', 'Primary API/cost references:','https://docs.kalshi.com/api-reference/market/get-market-candlesticks','https://docs.kalshi.com/getting_started/historical_data','https://docs.kalshi.com/getting_started/fee_rounding','https://kalshi.com/fee-schedule']
    (P/'REPORT.txt').write_text('\n'.join(out)+'\n')
if __name__=='__main__':main()
