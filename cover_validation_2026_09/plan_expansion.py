import sys,json
from pathlib import Path
from db_access import client,safe_error
P=Path(__file__).resolve().parent;symbols=json.loads((P/'new_symbols.json').read_text());plans=[]
for i in range(0,len(symbols),400):
 subset=symbols[i:i+400];f=P/f'new_symbology_{i:04d}.json'
 if not f.exists():
  try:r=client.symbology.resolve(dataset='XNAS.ITCH',symbols=subset,stype_in='raw_symbol',stype_out='instrument_id',start_date='2018-05-01',end_date='2026-09-12');f.write_text(json.dumps(r))
  except Exception as e:print('resolve error',i,safe_error(e),flush=True)
 for dataset in ['XNAS.ITCH','XNYS.PILLAR']:
  params=dict(dataset=dataset,symbols=subset,schema='statistics',start='2018-05-01',end='2026-09-12')
  try:
   quote=float(client.metadata.get_cost(**params));plans.append({'name':f'expansion_{dataset[:4]}_{i:04d}','params':params,'quote':quote});print(i,dataset,quote,flush=True)
  except Exception as e:plans.append({'name':f'expansion_{dataset[:4]}_{i:04d}','params':params,'error':safe_error(e)});print('quote error',i,dataset,safe_error(e),flush=True)
 (P/'EXPANSION_PLAN.json').write_text(json.dumps(plans,indent=2))
print('TOTAL',sum(x.get('quote',0) for x in plans),flush=True)
