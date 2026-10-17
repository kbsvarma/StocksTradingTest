from pathlib import Path
p=Path(__file__).resolve().parent
source=(p/'test_strategies.py').read_text();ns={'__file__':str(p/'test_strategies.py')};exec(source[:source.index('\nrows=[]\nfor t in M:')],ns)
import json,statistics
results=[]
for asset in ['LTC','ETH','BCH']:
 t='KX'+asset+'PERP';rows=[]
 for offset in [0,6,12,18]:
  orig=json.loads((p/'trades'/(t+'_trend24_72_o'+str(offset)+'.json')).read_text());controls={}
  for side in [1,-1]:
   trades=[ns['fill'](r['signal'],24,[(t,side,50)]) for r in orig];assert all(trades)
   controls[str(side)]=sum(r['net'] for r in trades)
  rows.append({'offset':offset,'trend':sum(r['net'] for r in orig),'always_long':controls['1'],'always_short':controls['-1']})
 results.append({'asset':asset,'schedules':rows,'median_long':statistics.median(r['always_long'] for r in rows),'median_short':statistics.median(r['always_short'] for r in rows)})
(p/'controls.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
