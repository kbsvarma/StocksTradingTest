"""Saved-ledger diagnostics; no search or changes to entry rules."""
import gzip,json,datetime as dt,math
from collections import defaultdict,Counter
import numpy as np
import study as s
def main():
 with gzip.open(s.P/'ledgers.json.gz','rt') as f:ledgers=json.load(f)
 out={};rng=np.random.default_rng(20261010)
 for key,rows in ledgers.items():
  if not key.endswith('/fees1c/full'):continue
  series=key.split('/')[0];start=s.ETHSTART if series=='KXETHD' else s.START
  dates=[s.etday(t) for t in range(start,s.END,86400)]
  byday=defaultdict(float)
  for x in rows:byday[s.etday(x['settled'])]+=x['net']
  values=np.array([byday[d] for d in dates]);samples=values[rng.integers(0,len(values),(20000,len(values)))].sum(axis=1)
  lower,upper=np.quantile(samples,[.025,.975]);losses=[x for x in rows if not x['won']]
  weekend=[x for x in rows if dt.datetime.fromtimestamp(x['t'],s.ET).weekday()>=5]
  weekday=[x for x in rows if dt.datetime.fromtimestamp(x['t'],s.ET).weekday()<5]
  # All-win resampling cannot represent unseen tail losses; mark this explicitly.
  out[key]=dict(calendar_days=len(dates),trades_per_calendar_day=len(rows)/len(dates),bootstrap_total_95=[float(lower),float(upper)],bootstrap_positive_fraction=float((samples>0).mean()),all_win_tail_unobserved=bool(rows and not losses),worst_days=sorted(byday.items(),key=lambda x:x[1])[:3],losses=losses,weekend=s.metrics(weekend),weekday=s.metrics(weekday),YES=s.metrics([x for x in rows if x['side']==1]),NO=s.metrics([x for x in rows if x['side']==-1]),average_entry=sum(x['entry'] for x in rows)/max(1,len(rows)),average_z=sum(x['z'] for x in rows)/max(1,len(rows)),minutes=dict(Counter(x['rem'] for x in rows)))
 s.save(s.P/'robustness.json',out)
 print(json.dumps({k:{x:v[x] for x in ('trades_per_calendar_day','bootstrap_total_95','all_win_tail_unobserved')} for k,v in out.items()},indent=2))
if __name__=='__main__':main()
