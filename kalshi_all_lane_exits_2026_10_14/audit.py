"""Independent scalar/vector parity, cash invariants, source and ledger checks."""
from portfolio import *
def main():
 data=pickle.load(gzip.open(P/'inputs.pkl.gz'));rr=data['actual'];checks={};cc=rules()
 assert len(rr)==459 and sum(not r['won'] for r in rr)==22
 assert abs(sum(r['hold_net'] for r in rr)-33.1242)<1e-6
 checks['ledger']={'trades':459,'wins':437,'losses':22,'net':33.1242}
 assert len({r['ticker'] for r in rr})==459
 allpool=candidates(list(data['markets'].values()));rows=pick(allpool,entry_configs()[0]);sample=rows[::max(1,len(rows)//150)];n=0
 for r in sample:
  p=path(r,data);vv=vector_exits(r,p,cc)
  for i,c in enumerate(cc):
   x=exit_result(r,c,p);assert abs(x['net']-vv[0][i])<1e-8,(r['ticker'],c['id'],x['net'],vv[0][i]);assert x['exit']==vv[1][i];n+=1
 checks['scalar_vector_comparisons']=n
 for f in ['actual_minute_results.json','actual_fast_results.json','depth_grid_results.json']:
  x=json.loads((P/f).read_text());checks[f]=len(x)
 coverage=json.loads((P/'actual_fast_coverage.json').read_text());assert len(coverage)==459 and sum(x['missing_source'] for x in coverage)==0
 checks['fast_actual_source_complete']=459
 xs=json.loads((P/'portfolio_results.json').read_text())
 for rec in xs:
  for m in rec['periods'].values():
   assert m['max_contracts']<=25 and m['max_open_principal']<=25+1e-6
   assert abs(sum(m['cash'].values())-100-m['net'])<1e-6
 checks['portfolio_scenarios']=len(xs);checks['portfolio_period_checks']=len(xs)*4
 depth=json.loads((P/'depth_grid_results.json').read_text());oldres=json.loads((R/'kalshi_fast_z_exit_2026_10_14/results.json').read_text())['actual'];n=0
 for x in depth:
  c=x['config']
  if c['family']=='drop' and c['value'] in [.5,1,1.5,2]:
   k=f"i{x['interval']}_d{c['value']:g}_g{c['grace']}_p{c['persist']}";o=next(y for y in oldres if y['id']==k);assert abs(x['metrics']['net']-o['net'])<1e-7;n+=1
 checks['prior_depth_reproduction']=n
 save('audit_results.json',checks);print(json.dumps(checks,indent=2))
if __name__=='__main__':main()
