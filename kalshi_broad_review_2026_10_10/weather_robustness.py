from common import *
from replay_candidates import portfolio,metrics,day
from collections import Counter
import statistics,random,math
candidates=read(P/'weather_model_candidates.json.gz');out={}
for lag in (10,6):
 key=f'persistence/{lag}/fees1c';rows=candidates[key]
 for seconds in (0,60,300,900):
  altered=[dict(r,settled=r['settled']+seconds) for r in rows];acc,blocked=portfolio(altered,.01);out[f'lag{lag}/release_plus{seconds}s']={'metrics':metrics(acc),'blocked':blocked}
 if lag==10:
  acc,_=portfolio(rows,.01);a=metrics(acc);p=sum(r['won'] for r in acc)/len(acc);n=len(acc);z=1.95996398454;den=1+z*z/n
  lo=(p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/den
  ds=[a['daily'].get(day(START+7*86400+i*86400),0) for i in range(7)];rand=random.Random(20261010);boot=sorted(sum(rand.choice(ds) for _ in range(7)) for __ in range(10000))
  out['diagnostics']={'avg_entry':statistics.mean(r['entry'] for r in acc),'avg_model_probability':statistics.mean(r['model_p'] for r in acc),'win_rate':p,'break_even_win_probability':p-a['net']/(5*n),'wilson95_lower_if_independent':lo,'net_without_best_day':a['net']-max(ds),'daily_bootstrap95':(boot[250],boot[9750]),'horizon_counts':dict(Counter(r['horizon'] for r in acc)),'mean_winner_net':statistics.mean(r['net'] for r in acc if r['won']),'mean_loser_net':statistics.mean(r['net'] for r in acc if not r['won']),'warning':'Only seven evaluation days. Bootstrap does not capture unseen regimes, tail losses, model selection or executable fills; independent Wilson assumption also optimistic.'}
save(P/'weather_robustness.json',out);print(json.dumps(out,indent=2))
