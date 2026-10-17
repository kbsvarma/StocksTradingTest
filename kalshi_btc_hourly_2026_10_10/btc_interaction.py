"""28-day BTC15m plus hourly replay with shared BTC position exclusivity."""
import gzip,json
import study as s
import followup as f
def main():
 with gzip.open(s.P/'opportunities.json.gz','rt') as stream:pools=json.load(stream)
 out={};ledgers={}
 for cost,(mode,stress) in s.SCENARIOS.items():
  base=s.select(pools['KXBTC15M'][mode],'late')
  variants={'paired_'+p:s.select(pools['KXBTCD'][mode],p) for p in s.PROFILES}
  variants.update({k:f.candidates(pools['KXBTCD'],k,mode) for k in f.RULES})
  variants.update({'range_'+p:s.select(pools['KXBTC'][mode],p) for p in ('late','extended20')})
  out[cost]={}
  for period,lo,hi in s.periods(s.START):
   current=[x for x in base if lo<=x['close']<hi];baseline,_=s.simulate(current,stress);bm=s.metrics(baseline)
   out[cost][period]={'BTC15m_alone':bm}
   for name,extra in variants.items():
    rows,blocks=s.simulate(current+[x for x in extra if lo<=x['close']<hi],stress);m=s.metrics(rows)
    kept={x['ticker'] for x in rows if x['series']=='KXBTC15M'}
    out[cost][period][name]=dict(**m,delta_trades=m['trades']-bm['trades'],delta_net=round(m['net']-bm['net'],4),hourly_entries=sum(x['series']!='KXBTC15M' for x in rows),displaced=sum(x['ticker'] not in kept for x in baseline),blocked=blocks)
    ledgers[f'{cost}/{period}/{name}']=rows
  print(cost,{k:(v['trades'],v['net'],v.get('delta_net')) for k,v in out[cost]['full'].items()},flush=True)
 seven={};lo,hi=s.ts('2026-09-23T00:00:00Z'),s.ts('2026-10-03T00:00:00Z')
 with gzip.open(s.P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as stream:old=json.load(stream)
 for cost,(mode,stress) in s.SCENARIOS.items():
  if cost=='delay1c':continue
  base=[dict(x,priority=0) for x in old[cost] if x['asset']!='COPPER' and lo<=x['close']<hi]
  current,_=s.simulate(base,stress);cm=s.metrics(current);seven[cost]={'base_seven':cm}
  for profile in ('late','extended20'):
   extra=[x for x in s.select(pools['KXBTC'][mode],profile) if lo<=x['close']<hi]
   accepted,blocked=s.simulate(base+extra,stress);am=s.metrics(accepted)
   seven[cost][profile]=dict(**am,delta_trades=am['trades']-cm['trades'],delta_net=round(am['net']-cm['net'],4),hourly_entries=sum(x.get('series')=='KXBTC' for x in accepted),blocked=blocked)
 s.save(s.P/'btc_interaction.json',dict(excludes_other_assets=True,results=out,range_seven_asset_matched_period=[lo,hi],range_seven_asset_results=seven))
 with gzip.open(s.P/'btc_interaction_ledgers.json.gz','wt') as stream:json.dump(ledgers,stream,separators=(',',':'))
if __name__=='__main__':main()
