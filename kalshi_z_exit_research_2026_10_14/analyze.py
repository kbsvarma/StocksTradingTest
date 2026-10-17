"""Matched-cohort summaries, exploratory robustness, and day-cluster uncertainty."""
import json,gzip,random,statistics
from pathlib import Path
from replay import grid,simulate,metrics
P=Path(__file__).resolve().parent
paths=json.load(gzip.open(P/'paths.json.gz'));oldpaths=json.load(gzip.open(P/'older_paths.json.gz'))
unknown=[r for r in paths['actual'] if any(p['z'] is None for p in r['path'])]
complete=[r for r in paths['actual'] if not any(p['z'] is None for p in r['path'])]
old=[r for k,v in oldpaths.items() if k.endswith('_btc_z300') for r in v]
configs=grid();candidates={c['id']:c for c in configs if c['id'] in ['entry_drop_2_wait120_persist2','entry_drop_2_wait180_persist2']}
out=dict(unknown_entries=[{k:r[k] for k in ['ticker','asset','won','hold_net']} for r in unknown],complete_entries=len(complete),complete_winners=sum(r['won'] for r in complete),matched_grid=[],candidate_checks={})
for c in configs:
 a=metrics([simulate(r,c) for r in complete]);o=metrics([simulate(r,c) for r in old])
 out['matched_grid'].append(dict(config=c,recent=a,older=o))
out['positive_both_count']=sum(x['recent']['delta']>0 and x['older']['delta']>0 for x in out['matched_grid'])
def bootstrap(rows):
 day=metrics(rows)['daily'];values=[x['delta'] for x in day.values()];rng=random.Random(20261014)
 draws=sorted(sum(rng.choice(values) for _ in values) for _ in range(20000))
 return dict(days=len(values),sum_delta=sum(values),paired_day_bootstrap95=[draws[499],draws[19499]],warning='Exploratory interval on reused data, not corrected for selecting 112 rules; cannot simulate unseen loss regimes.')
for name,c in candidates.items():
 out['candidate_checks'][name]={}
 for cohort,rs in [('recent_complete',complete),('current_design',paths['current_replay']),('older_current_crypto',old)]:
  rows=[simulate(r,c) for r in rs];result=dict(base=metrics(rows),assets={a:metrics([r for r in rows if r['asset']==a]) for a in sorted({r['asset'] for r in rows})},uncertainty=bootstrap(rows),ledger=rows)
  result['stress']={label:metrics([simulate(r,c,slip,lag,fs) for r in rs]) for label,slip,lag,fs in [('2c',.02,0,False),('5c',.05,0,False),('60sdelay',.01,60,False),('frozen_sigma',.01,0,True)]}
  if cohort=='older_current_crypto':
   result['periods']={k:metrics([simulate(r,c) for r in v]) for k,v in oldpaths.items() if k.endswith('_btc_z300')}
  out['candidate_checks'][name][cohort]=result
(P/'analysis.json').write_text(json.dumps(out,indent=2)+'\n')
print('complete',len(complete),'unknown',out['unknown_entries'],'positive both',out['positive_both_count'])
for n,co in out['candidate_checks'].items():
 for k,v in co.items():
  m=v['base'];print(n,k,{s:round(m[s],4) for s in ['hold_net','net','delta','loser_savings','winner_profit_sacrificed']},'cut',m['winners_cut'],'losers reduced',m['losers_improved'],'95',v['uncertainty']['paired_day_bootstrap95'])
  print('stress',[(s,round(mm['delta'],4)) for s,mm in v['stress'].items()])
  for per,mm in v.get('periods',{}).items():print('period',per,round(mm['delta'],4))
