"""Human-readable winner census; no trading recommendations or time filters."""
from pathlib import Path
import json, math, datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
rows=json.loads((P/'winners.json').read_text()); summary=json.loads((P/'summary.json').read_text())
audit=json.loads((P/'audit.json').read_text()); a=summary['primary']['all']
assets=list(summary['primary']['assets'])
def fmt(x):return 'N/A' if x is None else f'{x:+.4f}'
def stamp(x):return datetime.datetime.fromtimestamp(x,ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S ET')
lines=[
'WINNER Z RECOVERY CENSUS — NO TIME FILTERS',
'Research cutoff: markets closed through October 14, 2026, 22:00 ET.',
'438 actual settled winners, September 12–October 14, including paused Copper.',
'USD/CAD has no winning trades in this ledger; it cannot supply a winner boundary.',
'Both strategy lanes are included: original 375 winners, premium 63 winners.',
'The initial 437-winner cohort gained one WTI premium winner while the audit ran.',
'',
'ANSWER',
'Winners did not stop recovering at a common positive z threshold. At one-second',
'checks, 90 eventual winners crossed below z=1, 45 below 0.5, and 21 below zero.',
'Of those 21, nineteen rebounded to positive modeled z, one EUR/USD reached zero,',
'and one BTC stayed negative in the proxy model but nevertheless settled a winner.',
'The deepest one-second modeled z was -6.8365; finer crypto ticks show -8.2737.',
'No winner crossed below -8.5 in the finest available observations. This is only',
'an observed sample boundary, not a validated universal no-recovery threshold.',
'It does not demonstrate that a stop there would recover any useful cash.',
'',
'DEFINITION AND COVERAGE',
'Signed held-side z: positive supports the originally purchased side; negative',
'means the underlying crossed the strategy reference against that side. Keep',
'the purchased YES/NO side fixed; do not flip to the new favourite after a reversal.',
'z(t) = side * (spot(t)/entry_reference - 1) /',
'       (entry_sigma * sqrt((close_timestamp - t)/60)).',
'Primary: one-second checks over every post-entry/pre-close second, frozen entry',
'sigma, historical source prices with assumed one-second availability. Crypto',
'uses CF index changes anchored to the recorded entry Coinbase spot; other assets',
'use the historical Kalshi/Pyth prices directly. This reconstructs z, not a',
'continuous record of production z or measured historical feed-arrival times.',
'No minimum holding time, confirmation wait, expiry-window or clock-time filter.',
'The ordinary remaining-time term in the z formula is retained. No paths or',
'near-expiry dips were dropped. No exit prices, fills or P&L are simulated here.',
f'Primary coverage: {audit["primary_observations"]:,} finite observations across all 438 winners.',
f'Finer source check: {audit["finest_observations"]:,} observations; crypto every 0.2 seconds,',
'other assets every second. No interpolation or missing-price forward filling.',
'',
'LOWEST Z REACHED BY AN EVENTUAL WINNER, BY ASSET',
'Asset      Winners   Minimum at 1s   Minimum finest   Empty threshold*',
]
for asset in assets:
    v=summary['primary']['assets'][asset];f=summary['finest']['assets'][asset]
    empty=math.floor(f['minimum']*4)/4 if f['minimum'] is not None else None
    lines.append(f'{asset:10s} {v["winners"]:7d} {fmt(v["minimum"]):>15s} {fmt(f["minimum"]):>16s} {fmt(empty):>18s}')
lines += ['*No winning path crossed strictly below this quarter-z boundary in the',
          'finest observations. The boundary was chosen AFTER seeing the minimum.',
          'It is descriptive, not a tested stop; zero USD/CAD winners is not evidence.',
          '', 'COUNTS OF DISTINCT EVENTUAL WINNERS BELOW ABSOLUTE Z THRESHOLDS',
          'Threshold    1s count   % of 438    Finest count']
for k in ['3','2.5','2','1.75','1.5','1.25','1','0.75','0.5','0.25','0','-0.25','-0.5','-0.75','-1','-1.5','-2','-2.5','-3','-6','-8','-10']:
    n=a['below'][k];nf=summary['finest']['all']['below'][k]
    lines.append(f'{float(k):+9.2f} {n:11d} {n/len(rows)*100:10.2f}% {nf:15d}')
lines += ['Cumulative counts, not disjoint buckets. A threshold crossing is not an',
          'executed stop or proof the trade would have been sold at a loss.',
          '', 'MAXIMUM DROP BELOW ENTRY Z AMONG WINNERS',
          'Entry-z drop   1s count   % of 438    Finest count']
for k in ['0.25','0.5','0.75','1','1.5','2','2.5','3','4','5','10']:
    n=a['drops'][k];nf=summary['finest']['all']['drops'][k]
    lines.append(f'{float(k):12.2f} {n:10d} {n/len(rows)*100:10.2f}% {nf:15d}')
lines += ['', 'EACH ASSET / STRATEGY LANE (1-SECOND MODEL)',
          'Asset      Strategy  Winners  Lowest z  Below 1  Below .5  Below 0']
for asset in assets:
    for lane,label in [('cushion_z2','original'),('premium_z3','premium')]:
        rr=[r for r in rows if r['asset']==asset and r['lane']==lane]
        low=min(r['primary']['minimum'] for r in rr) if rr else None
        nn=[sum(r['primary']['minimum']<v for r in rr) for v in [1,.5,0]]
        lines.append(f'{asset:10s} {label:8s} {len(rr):7d} {fmt(low):>9s} {nn[0]:8d} {nn[1]:9d} {nn[2]:8d}')
lines += ['', 'THE EXTREME BTC WINNER IS A SETTLEMENT/REFERENCE EXCEPTION',
          'KXBTC15M-26OCT111030-30, purchased NO, actual net +$0.3601.',
          'Entry z +2.0399. One-second minimum -6.8365; finest minimum -8.2737.',
          'It did NOT rebound above zero in the modeled post-nadir path.',
          'The archived exchange receipt confirms the official target $83,008.51',
          'and final settlement average $83,003.59, which correctly settles NO.',
          'Our strategy reference was $82,992.78, with a different source model.',
          'The formula also magnifies a small adverse reference gap near expiry',
          'because its remaining-time denominator shrinks toward zero.',
          'Do not call this a recovery from -8.27 or remove it to make a stop look safe.',
          'Among negative paths that actually rebounded above zero, the deepest',
          'was EUR/USD -2.6691. That narrower statement does NOT erase the BTC winner.',
          '', 'SOURCE/MODEL SENSITIVITY (SEPARATE CONTROLS, NO TIME OPTIMIZATION)',
          'Same one-second prices with rolling completed-minute sigma: 434 complete',
          'winning paths, four without valid rolling observations, 1,611 missing',
          'observations retained explicitly. Lowest z -6.7871; 21 below zero.',
          'Completed-minute source/rolling-sigma control: 426 observable winners,',
          '12 without usable observations (nine entered too late for a subsequent',
          'completed-minute check; three lack rolling inputs). 23 missing minute',
          'observations retained. Lowest z -2.2088 and only 11 below zero.',
          'The minute control misses intraminute dips; it cannot establish a safer',
          'lower boundary. Primary and finer paths cover all 438 winners.',
          '', 'VALIDATION AND PRESERVATION',
          'All 438 winners match the read-only live ledger through the fixed cutoff',
          'and archived exchange outcomes. Entry-z formula reproduces every entry',
          'exactly; independently checked each threshold against each saved path.',
          'Each asset minimum matches raw source receipts. No financial order',
          'requests, live/paper bot edits, parameter changes or service restarts.',
          'Live code and arm/halt flags retain their previous audit hashes.',
          '', 'FILES',
          'winners.json: all 438 entry/minimum/drop/coverage/source-evidence records.',
          'WINNERS.txt: readable complete list of winners, sorted by minimum z.',
          'paths.pkl.gz: complete reconstructed primary and finer paths.',
          'summary.json: pooled, asset and strategy summaries for every definition.',
          'audit.json, raw_nadir_checks.json, live_ledger_check.json: validation.',
          'btc_settlement_exception.json: original exchange metadata and rule text.',
          'manifest.json: source hashes; source receipts retained in this and prior',
          'kalshi_all_lane_exits_2026_10_14 experiment directories.',
          '', 'CONCLUSION',
          'There are asset-specific sample floors to investigate, but this winner',
          'census alone does not prove a loss-saving stop. Small entry-z drops would',
          'trigger on many eventual winners. Test any chosen level against losses',
          'and executable prices in a separate step; no time filters were fitted here.']
(P/'REPORT.txt').write_text('\n'.join(lines)+'\n')
lines=['ALL 438 WINNERS — NO TIME FILTERS',
       'ticker | asset | strategy | entry z | minimum 1s z | drop | finest min | min time ET | source count',
       'Negative z is against the purchased side. Winning settlement is not always a positive-z recovery.']
for r in sorted(rows,key=lambda x:x['primary']['minimum']):
    v=r['primary'];lines.append(f'{r["ticker"]} | {r["asset"]} | {r["lane"]} | {r["entry_z"]:+.5f} | {v["minimum"]:+.5f} | {v["entry_to_min_drop"]:.5f} | {r["finest"]["minimum"]:+.5f} | {stamp(v["min_ts"])} | {v["observations"]}')
(P/'WINNERS.txt').write_text('\n'.join(lines)+'\n')
print('Wrote report and all-winner list.')
