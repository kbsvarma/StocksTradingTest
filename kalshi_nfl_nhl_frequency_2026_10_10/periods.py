"""NHL period winner frequency comparison; three-way YES outcomes only."""
from study import *
from concurrent.futures import ThreadPoolExecutor,as_completed

def main(spread_max=.02):
 grouped=defaultdict(list);receipt=[];series={}
 for per in [1,2,3]:
  s=f'KXNHL{per}P';series[s]=get('/series/'+s)['series'];save(P/'period_data'/(s+'_series.json'),series[s])
  for m in read(P/'period_probe'/(s+'_markets.json')):
   et=m['event_ticker'].replace(s,'KXNHLGAME');grouped[et].append((per,m))
 d=P/'data/KXNHLGAME';full=defaultdict(list)
 for m in read(d/'markets.json'):full[m['event_ticker']].append(m)
 def collect(et,items):
  if et not in full:return {'event':et,'status':'no_completed_fullgame_source','contracts':len(items)}
  mi=read(d/(et+'_milestone.json'));start=ts(mi['start_date']);end=min(max(ts(m['close_time']) for _,m in items)+120,start+8*3600)
  cp=P/'period_data'/(et+'_candles.json.gz')
  if not cp.exists():
   z=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for _,m in items),'start_ts':start-60,'end_ts':end,'period_interval':1});save(cp,{r['market_ticker']:r['candlesticks'] for r in z.get('markets',[])})
  return {'event':et,'status':'saved','contracts':len(items)}
 with ThreadPoolExecutor(max_workers=3) as pool:
  for f in as_completed([pool.submit(collect,et,items) for et,items in grouped.items()]):
   try:receipt.append(f.result())
   except Exception as ex:receipt.append({'status':'error','error':str(ex)})
 save(P/'period_collection.json',receipt);print('COLLECTION',dict(Counter(x['status'] for x in receipt)),flush=True)
 signals=defaultdict(list);coverage=Counter()
 for et,items in grouped.items():
  cp=P/'period_data'/(et+'_candles.json.gz')
  if et not in full or not cp.exists():continue
  mi=read(d/(et+'_milestone.json'));y=read(d/(et+'_espn.json.gz'));states,stamps,end,home,away,*_=prepare(full[et],read(d/(et+'_candles.json.gz')),mi,'KXNHLGAME',y)
  cm=read(cp);csmap={t:{c['end_period_ts']:c for c in cs} for t,cs in cm.items()}
  for cs in csmap.values():
   for t,c in cs.items():c['_volume10']=sum(float(cs.get(tt,{}).get('volume_fp') or cs.get(tt,{}).get('volume') or 0) for tt in range(t-540,t+60,60))
  for per in [1,2,3]:
   markets=[m for p,m in items if p==per];pst=[r for r in states if r['period']==per]
   if not pst or not markets:continue
   before=[r for r in states if r['period']<per and r['wall']<pst[0]['wall']]
   if per>1 and not before:coverage['missing_period_start_score']+=1;continue
   h0,a0=(0,0) if per==1 else (before[-1]['home'],before[-1]['away']);pend=max(r['wall'] for r in pst);coverage['game_periods']+=1
   # Audit resolved period winner against observed incremental goals.
   dh=pst[-1]['home']-h0;da=pst[-1]['away']-a0
   for m in markets:
    team=next(iter(m['custom_strike'].values()));expected=int(dh>da) if team==home else int(da>dh) if team==away else int(dh==da)
    if payout(m,'yes')!=expected:coverage['period_settlement_mismatch']+=1
   for label,lag,mode,friction in SCENARIOS:
    if label=='cost2c':continue
    for t in sorted({t for m in markets for t in csmap.get(m['ticker'],{}) if pst[0]['wall']<=t<pend}):
     r=at(states,stamps,t,lag)
     if not r or r['period']!=per or t-r['wall']>120 or not 0<r['clock']<=300:continue
     diff=(r['home']-h0)-(r['away']-a0)
     if not diff:continue
     team=home if diff>0 else away
     for band,(low,high) in {'wide90':(.90,.98),'mid85':(.85,.95)}.items():
      choices=[]
      for m in markets:
       if next(iter(m['custom_strike'].values()))!=team:continue
       cs=csmap.get(m['ticker'],{});q=quote(cs.get(t))
       if not q or not low<=q[1]<high or q[1]-q[0]>spread_max+1e-8 or cs[t]['_volume10']<5:continue
       choices.append((q[1],m['ticker'],m,cs,q[0]))
      if not choices:continue
      ask,ticker,m,cs,bid=min(choices,key=lambda a:a[:2]);entered=t+(60 if mode=='close' else 3);r2=at(states,stamps,entered,lag)
      if not r2 or r2['period']!=per or entered-r2['wall']>120 or not 0<r2['clock']<=300:continue
      diff2=(r2['home']-h0)-(r2['away']-a0)
      if diff*diff2<=0 or entered>=min(pend,ts(m['close_time']),ts(m['settlement_ts'])):continue
      q2=quote(cs.get(t+60),phase=mode)
      if not q2:continue
      entry=max(ask,q2[1]);cap=min(.98,ask+.01)
      if entry>cap+1e-8:continue
      s=f'KXNHL{per}P';mult=series[s]['fee_multiplier'];pay=payout(m,'yes')
      row={'kind':'sports','asset':s,'event':m['event_ticker'],'ticker':ticker,'side':'yes','game_owner':'NHL/'+mi['id']+'/period'+str(per),'t':t,'entered':entered,'entry':entry,'ask':ask,'bid':bid,'cap':cap,'payout':pay,'won':pay==1 if pay in (0,1) else None,'settled':ts(m['settlement_ts']),'close':ts(m['close_time']),'game_end':pend,'fee':fee(entry,mult),'source_delay':lag,'score_wall':r2['wall'],'score_age':entered-r2['wall'],'period':per,'period_home':r2['home']-h0,'period_away':r2['away']-a0,'max_clock':max(r['clock'],r2['clock']),'minimum_lead':min(abs(diff),abs(diff2)),'season_type':mi['details']['season_type']}
      signals[band+'/'+label].append(row)
 results={};ledgers={}
 for band in ['wide90','mid85']:
  for lead in [1,2]:
   for mins in [2,5]:
    for label,lag,mode,friction in SCENARIOS:
     candidates=[r for r in signals[band+'/'+('base' if label=='cost2c' else label)] if r['minimum_lead']>=lead and r['max_clock']<=60*mins]
     for scope in ['ALL','P1','P2','P3']:
      for season in ['REG','PRE']:
       rows=[r for r in candidates if (scope=='ALL' or r['period']==int(scope[1:])) and r['season_type']==season];a,b=portfolio(rows,friction);key=f'{band}/lead{lead}/m{mins}/{label}/{scope}/{season}';results[key]={**metrics(a),'blocked':b};ledgers[key]=a
 prefix='period' if spread_max==.02 else f'period_spread{round(spread_max*100)}'
 save(P/(prefix+'_results.json'),results);save(P/(prefix+'_ledgers.json.gz'),ledgers);save(P/(prefix+'_candidates.json.gz'),signals);save(P/(prefix+'_coverage.json'),dict(coverage));print('COVERAGE',coverage)
 for k,v in results.items():
  if '/base/ALL/REG' in k:print(k,v['trades'],v['wins'],v['losses'],v['net'])
 assert not coverage['period_settlement_mismatch']
if __name__=='__main__':
 import sys
 main(float(sys.argv[1]) if len(sys.argv)>1 else .02)
