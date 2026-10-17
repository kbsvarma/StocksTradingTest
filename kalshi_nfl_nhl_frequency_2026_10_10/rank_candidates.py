"""Prior-training-only ranking fixed before validation outcomes were examined."""
from study import *
results=read(P/'results.json');out={}
for s,families in FAMILIES.items():
 rows=[]
 for f in families:
  for minutes in [5,10,15]:
   for band in ['p90','p95','wide']:
    stem=f'{s}/{f}/m{minutes}/{band}';parts={label:results[stem+'/'+label+'/previous_early'] for label,_,_,_ in SCENARIOS}
    ok=parts['base']['trades']>=25 and all(v['net']>0 for v in parts.values())
    rows.append({'rule':stem,'eligible':ok,'minimum_training_stress_net':min(v['net'] for v in parts.values()),'training_trades':parts['base']['trades'],'training_net':parts['base']['net']})
 ranked=sorted(rows,key=lambda v:(v['eligible'],v['minimum_training_stress_net'],v['training_trades']),reverse=True)
 chosen=next((v['rule'] for v in ranked if v['eligible']),None)
 out[s]={'selected':chosen,'ranking':ranked,'comparisons':{part:{label:results[chosen+'/'+label+'/'+part] for label,_,_,_ in SCENARIOS} for part in ['previous_early','previous_late','recent','recent_preseason','all_regular']} if chosen else {}}
 print('SELECTED',s,chosen)
 if chosen:
  for part,x in out[s]['comparisons'].items():print(part,[(sc,v['trades'],v['wins'],v['losses'],v['net']) for sc,v in x.items()])
 else:print('NO RULE CLEARED PRIOR TRAINING SCREEN',ranked[:3])
save(P/'selection.json',out)
