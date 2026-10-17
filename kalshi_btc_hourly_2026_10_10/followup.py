"""Post-discovery lane isolation/persistence replay; explicitly exploratory."""
import gzip,json
import study as s
RULES={**{lane+'_'+profile:(lane,profile) for lane in ('original','premium') for profile in ('late','extended20','extended30')},'persistent20':('both','extended20')}
def candidates(pools,name,mode):
 lane,profile=RULES[name];rows=s.select(pools[mode],profile)
 if lane!='both':return [x for x in rows if x['lane']==(s.Z2 if lane=='original' else s.Z3)]
 previous={(x['market_ticker'],x['side'],x['lane'],x['t']) for x in s.select(pools['opening'],profile)}
 return [x for x in rows if (x['market_ticker'],x['side'],x['lane'],x['t']-60) in previous]
def main():
 with gzip.open(s.P/'opportunities.json.gz','rt') as f:allpools=json.load(f)
 pools=allpools['KXBTCD'];result={};ledgers={};combined={}
 with gzip.open(s.P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:old=json.load(f)
 lo,hi=s.ts('2026-09-23T00:00:00Z'),s.ts('2026-10-03T00:00:00Z')
 for name in RULES:
  result[name]={};combined[name]={}
  for cost,(mode,stress) in s.SCENARIOS.items():
   rows=candidates(pools,name,mode);result[name][cost]={}
   for period,a,b in s.periods(s.START):
    accepted,blocks=s.simulate([x for x in rows if a<=x['close']<b],stress)
    result[name][cost][period]=dict(**s.metrics(accepted),blocked=blocks);ledgers[f'{name}/{cost}/{period}']=accepted
   if cost!='delay1c':
    base=[dict(x,priority=0) for x in old[cost] if x['asset']!='COPPER' and lo<=x['close']<hi]
    b,_=s.simulate(base,stress);bm=s.metrics(b)
    a,blocks=s.simulate(base+[x for x in rows if lo<=x['close']<hi],stress);am=s.metrics(a)
    kept={x['ticker'] for x in a if x.get('series')!='KXBTCD'}
    combined[name][cost]=dict(**am,delta_trades=am['trades']-bm['trades'],delta_net=round(am['net']-bm['net'],4),hourly_entries=sum(x.get('series')=='KXBTCD' for x in a),displaced=sum(x['ticker'] not in kept for x in b),blocked=blocks)
  print(name,{cost:{p:(v['trades'],v['wins'],v['losses'],v['net']) for p,v in periods.items()} for cost,periods in result[name].items()},flush=True)
 s.save(s.P/'followup_results.json',dict(exploratory_post_discovery=True,results=result,integration=combined))
 with gzip.open(s.P/'followup_ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
if __name__=='__main__':main()
