"""One bounded public-data snapshot. No credentials, orders, or service loop."""
from common import *
from collections import defaultdict

now=time.time();x=get('/markets',{'series_ticker':'KXMLBGAME','status':'open','limit':1000},cache=False)
assert not x.get('cursor'),'Snapshot must not silently truncate markets.'
groups=defaultdict(list)
for m in x['markets']:groups[m['event_ticker']].append(m)
out=[]
for et,ms in sorted(groups.items()):
 miles=get('/milestones',{'related_event_ticker':et,'limit':100},cache=False)
 selected=[m for m in miles['milestones'] if (m.get('details') or {}).get('main_game_event_ticker')==et]
 item={'event':et,'snapshot_ts':now,'milestone_matches':len(selected),
       'markets':[{'ticker':m['ticker'],'bid':m.get('yes_bid_dollars'),'ask':m.get('yes_ask_dollars'),'ask_size':m.get('yes_ask_size_fp'),'volume24':m.get('volume_24h_fp')} for m in ms]}
 if len(selected)==1:
  mile=selected[0];item.update(start=mile['start_date'],end=mile.get('end_date'))
  if ts(mile['start_date'])<=now and (not mile.get('end_date') or ts(mile['end_date'])>=now):
   pb=get('/live_data/milestone/'+mile['id']+'/game_stats',cache=False);ev=[]
   for per in (pb.get('pbp') or {}).get('periods') or []:
    for e in per.get('events') or []:
     if e.get('wall_clock'):ev.append({'wall':e['wall_clock'],'period':per.get('period_number'),'home':e.get('home_points'),'away':e.get('away_points'),'half':e.get('half') or per.get('half'),'outs':e.get('outs')})
   item['latest_timestamped_play']=max(ev,key=lambda r:int(r['wall'])) if ev else None
   if ev:item['score_age_seconds']=time.time()-int(item['latest_timestamped_play']['wall'])
   item['source_has_pbp']=bool((pb.get('pbp') or {}).get('periods'))
 out.append(item)
save(P/'current_probe.json',{'as_of_ts':now,'events':out,'qualification':'Descriptive snapshot, not a fully initialized strategy or verified fill. Pregame favorite history and depth at a qualifying signal remain required.'})
print(json.dumps(out,indent=2))
