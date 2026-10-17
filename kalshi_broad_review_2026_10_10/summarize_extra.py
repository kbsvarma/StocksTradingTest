from common import *
from collections import Counter
fx=read(P/'combined_fx_ledgers.json.gz')['plus1c/full'];rows=read(P/'combined_fx_results.json')['plus1c/full']['combined'];cost=1-rows['net']/(5*len(fx));n=len(fx)
from replay_candidates import day
summary={'fx_dates':dict(Counter(day(r['entered']) for r in fx)),'fx_unique_expiry_times':len({r['close'] for r in fx}),'fx_expiry_overlap':dict(Counter(Counter(r['close'] for r in fx).values())),'fx_one_sided95_all_win_lower_bound_if_independent':.05**(1/n),'fx_average_break_even_win_probability':cost,'warning':'Independent Bernoulli calculation is illustrative; simultaneous USD exposure violates independence and weakens confidence.'}
save(P/'extra_summary.json',summary);print(summary)
