"""Two bounded, read-only feed/book snapshots, twenty seconds apart."""
from common import *
from collect import canon
from collections import defaultdict
from zoneinfo import ZoneInfo

out=[]
for snap in range(2):
 now=time.time();url='https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates=20261010&limit=100';resp=requests.get(url,timeout=25).json();save(P/'live_probe_raw'/f'schedule_{snap}.json',{'url':url,'received':time.time(),'response':resp})
 markets=get('/markets',{'series_ticker':'KXNHLGAME','status':'open','limit':1000},cache=False);assert not markets.get('cursor')
 groups=defaultdict(list)
 for m in markets['markets']:groups[m['event_ticker']].append(m)
 games=[g for g in resp.get('events',[]) if (g.get('status') or {}).get('type',{}).get('state')=='in' and (g.get('season') or {}).get('type')==2]
 games=sorted(games,key=lambda g:((g.get('status') or {}).get('period',0),-(float((g.get('status') or {}).get('clock',1200)))),reverse=True)[:3]
 for g in games:
  url='https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event='+g['id'];y=requests.get(url,timeout=25).json();received=time.time();save(P/'live_probe_raw'/(g['id']+f'_{snap}.json'),{'url':url,'received':received,'response':y})
  plays=[p for p in y.get('plays',[]) if p.get('wallclock')];last=max(plays,key=lambda p:ts(p['wallclock'])) if plays else None
  parts={p['homeAway']:canon(p['team']['abbreviation'],'KXNHLGAME') for p in g['competitions'][0]['competitors']};matches=[(e,ms) for e,ms in groups.items() if '26OCT10' in e and {canon(m['ticker'].rsplit('-',1)[1],'KXNHLGAME') for m in ms}==set(parts.values())]
  row={'snapshot':snap,'espn_id':g['id'],'name':g.get('shortName'),'received':received,'status':g['status'],'latest_play':{k:last.get(k) for k in ['id','wallclock','modified','homeScore','awayScore','period','clock']} if last else None,'event_matches':len(matches),'source_age':received-ts(last['wallclock']) if last else None}
  if len(matches)==1:
   et,ms=matches[0];row['event']=et;row['markets']=[{'ticker':m['ticker'],'yes_ask':m.get('yes_ask_dollars'),'yes_bid':m.get('yes_bid_dollars'),'ask_size':m.get('yes_ask_size_fp')} for m in ms]
   row['books']={m['ticker']:get('/markets/'+m['ticker']+'/orderbook',{'depth':10},cache=False) for m in ms}
  out.append(row)
 if snap==0:time.sleep(20)
save(P/'current_probe.json',{'snapshots':out,'orders_sent':0,'limit':'Two descriptive snapshots do not prove historical latency or fills at qualifying signals.'})
print(json.dumps([{k:v for k,v in r.items() if k!='books'} for r in out],indent=2))
