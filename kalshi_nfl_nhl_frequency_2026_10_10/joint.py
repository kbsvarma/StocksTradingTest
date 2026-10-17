"""Fixed NFL/period candidate combination, one shared $100. Offline only."""
from study import *

nfl = read(P/'operational_candidates_spread5.json.gz')
nhl = read(P/'period_spread5_candidates.json.gz')
results, ledgers = {}, {}
for label, lag, mode, friction in SCENARIOS:
    source = 'base' if label == 'cost2c' else label
    nr = [r for r in nfl['KXNFLGAME/p90/'+source]
          if accepts(r, 'leader', 5) and period(r, 'recent', 'KXNFLGAME')]
    hr = [r for r in nhl['wide90/'+source]
          if r['minimum_lead'] >= 1 and r['max_clock'] <= 300
          and r['season_type'] == 'REG']
    for window, begin in [('recent', '2026-09-01'), ('october', '2026-10-01')]:
        rows = [r for r in nr+hr if day(r['entered']) >= begin]
        actual, blocked = portfolio(rows, friction)
        key = label+'/'+window
        results[key] = {**metrics(actual), 'blocked': blocked,
                       'by_league': {league: metrics([r for r in actual if r['asset'].startswith(league)])
                                     for league in ['KXNFL', 'KXNHL']}}
        ledgers[key] = actual
save(P/'joint_results.json', results)
save(P/'joint_ledgers.json.gz', ledgers)
for key, value in results.items():
    print(key, value['trades'], value['wins'], value['losses'], value['net'], value['blocked'])
