"""Explicit original-only sensitivity; retain every premium trade at holding."""
from pathlib import Path
import gzip,json,sys
P=Path(__file__).resolve().parent
import finalize as f
allrows=json.load(gzip.open(P/'selected_ledgers.json.gz','rt'));base={r['ticker']:r for r in allrows['prior_map']};out={};ledgers={}
for name in ['WTI_only_addition','NATGAS_harvest_addition','WTI_NATGAS_additions','BTC_WTI_NATGAS_additions']:
 xs=[dict(r if r['lane']=='cushion_z2' else base[r['ticker']]) for r in allrows[name]]
 assert len(xs)==461 and sum(not x['won'] for x in xs)==23
 for r in xs:
  if r['lane']=='premium_z3':assert r['net']==r['hold_net']
 out[name]=f.summarize(xs);ledgers[name]=xs
 for m in out[name]['periods'].values():assert abs(m['net']-m['hold']-m['loss_savings']+m['winner_cost'])<1e-9
(P/'original_only_results.json').write_text(json.dumps(out,indent=2)+'\n')
with gzip.open(P/'original_only_ledgers.json.gz','wt') as h:json.dump(ledgers,h)
for k,v in out.items():print(k,json.dumps(v['periods']))
