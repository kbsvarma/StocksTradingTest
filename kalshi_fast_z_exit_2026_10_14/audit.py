from pathlib import Path
import json,pickle,gzip,bisect,math,statistics
from replay import simulate,prepare,metrics
P=Path(__file__).resolve().parent
def main():
 raw=pickle.load(gzip.open(P/'inputs.pkl.gz'));led=json.load(gzip.open(P/'ledgers.json.gz'));summary=json.loads((P/'results.json').read_text());count=exits=0
 for group,configs in led.items():
  rr={r['ticker']:r for r in raw['cohorts'][group]}
  for key,rows in configs.items():
   expected=next(x for x in summary[group] if x['id']==key);mm=metrics(rows)
   for k,v in mm.items():assert abs(v-expected[k])<1e-8,(key,k)
   for x in rows:
    count+=1;r=rr[x['ticker']];assert abs(r['quantity']*r['won']-r['cost']-r['entry_fee']-r['entry_friction']-x['hold_net'])<1e-7
    if not x['exit']:continue
    exits+=1;b=raw['bundles'][x['ticker']];cf=b['cf']['5hz'];ct=[r[0] for r in cf];anchor=cf[bisect.bisect_right(ct,r['t']*1000)-1];obs=cf[bisect.bisect_right(ct,x['trigger']*1000)-1]
    z=r['side']*(r['entry_spot']*obs[2]/anchor[2]/r['reference']-1)/(r['entry_sigma']*math.sqrt((r['close']-x['trigger'])/60))
    assert abs(z-x['trigger_z'])<1e-8 and z<x['stop_z']
    assert 0<=x['trigger']*1000-obs[1]<=1500
    assert x['entered']<x['trigger']<x['book_after']<x['close'];assert x['book_before']<=x['trigger']
    assert x['trigger']-x['entered']>=expected['grace'];assert x['trigger']-x['breach_start']>=expected['persistence']
    assert x['trigger']%expected['interval']==0
    bt=[r[0]/1000 for r in b['books']];pre=bisect.bisect_right(bt,x['trigger'])-1;post=bisect.bisect_left(bt,x['trigger']+1)
    assert bt[pre]==x['book_before'] and bt[post]==x['book_after']
    assert sum(q for p,q in x['execution']['levels'])==x['quantity']
    assert abs(x['execution']['proceeds']-x['quantity']*.01-x['cost']-x['entry_fee']-x['entry_friction']-x['net'])<1e-8
 phases={}
 for group,cohort in raw['cohorts'].items():
  prepared=[prepare(r,raw['bundles'][r['ticker']],raw['prices'][r['asset']]) for r in cohort];phases[group]={}
  for interval in (1,10,30,60):
   for drop in (1,2):
    out=[metrics([simulate(r,interval,drop,0,0,phase=phase) for r in prepared]) for phase in range(interval)]
    phases[group][f'i{interval}_d{drop}']=dict(min_net=min(x['net'] for x in out),mean_net=statistics.mean(x['net'] for x in out),max_net=max(x['net'] for x in out),all=out)
 (P/'audit.json').write_text(json.dumps(dict(passed=True,scenario_rows=count,exits_checked=exits),indent=2)+'\n');(P/'phase_sensitivity.json').write_text(json.dumps(phases,indent=2)+'\n');print('PASS',count,'rows',exits,'exits')
if __name__=='__main__':main()
