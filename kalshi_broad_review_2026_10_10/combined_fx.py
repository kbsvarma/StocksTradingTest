from analyze_fx import *
markets=pickle.loads((P/'extension/features.pickle').read_bytes())
r.ASSET_ORDER=tuple(dict.fromkeys(r.ASSET_ORDER+('AUDUSD','USDCAD')));r.qualifies=rule('eurusd_transfer')
out={};ledgers={}
for label,mode,stress in SCENARIOS:
 rows,counts=r.opportunities(markets,mode)
 for period,lo,hi in [('full',0,END),('new_since_oct9_11ET',OLD_END,END)]:
  selected=[x for x in rows if lo<=x['close']<hi]
  acc,blocked=r.portfolio(selected,stress)
  out[label+'/'+period]={'combined':r.metrics(acc),'blocked':blocked,'by_asset':{a:r.metrics([x for x in acc if x['asset']==a]) for a in ('AUDUSD','USDCAD')}};ledgers[label+'/'+period]=acc
save('combined_fx_results.json',out)
with gzip.open(P/'combined_fx_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
print(json.dumps(out,indent=2))
