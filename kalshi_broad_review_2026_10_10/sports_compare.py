"""Match sportsbook pregame outcomes to Kalshi contracts; no orders."""
import json,gzip,re,statistics,unicodedata,time,math
from collections import Counter
from pathlib import Path
from common import P,read,save,ts,get
MAP={'americanfootball_nfl':'KXNFLGAME','americanfootball_ncaaf':'KXNCAAFGAME','basketball_nba':'KXNBAGAME','icehockey_nhl':'KXNHLGAME','baseball_mlb':'KXMLBGAME','soccer_epl':'KXEPLGAME'}
ALIASES={'Los Angeles D':'Los Angeles Dodgers','Los Angeles A':'Los Angeles Angels','LA Lakers':'Los Angeles Lakers','LA Clippers':'Los Angeles Clippers','Los Angeles C':'Los Angeles Clippers','Los Angeles L':'Los Angeles Lakers','NY Giants':'New York Giants','NY Jets':'New York Jets','New York G':'New York Giants','New York J':'New York Jets','LA Rams':'Los Angeles Rams','LA Chargers':'Los Angeles Chargers','Los Angeles R':'Los Angeles Rams','Los Angeles Ch':'Los Angeles Chargers','NC St.':'NC State','Miami FL':'Miami Hurricanes','Miami OH':'Miami Ohio','Ole Miss':'Mississippi Rebels','USC':'USC Trojans','UConn':'Connecticut Huskies','UCF':'UCF Knights','TCU':'TCU Horned Frogs','SMU':'SMU Mustangs','UTSA':'UTSA Roadrunners','BYU':'BYU Cougars','Boise St.':'Boise State','San Jose St.':'San Jose State','Fresno St.':'Fresno State','App St.':'Appalachian State','UL Monroe':'Louisiana Monroe','UL Lafayette':'Louisiana Ragin Cajuns','Man City':'Manchester City','Man United':'Manchester United','Nott Forest':'Nottingham Forest','Wolverhampton':'Wolverhampton Wanderers','Brighton':'Brighton and Hove Albion','Spurs':'Tottenham Hotspur'}
def norm(s):return re.sub('[^a-z0-9]','',unicodedata.normalize('NFKD',s.lower()).encode('ascii','ignore').decode())
def matchname(k,o):
 k=ALIASES.get(k,k);a=norm(k);b=norm(o)
 return a==b or (len(a)>=4 and b.startswith(a))
def fee(p,m):return math.ceil((.07*m*5*p*(1-p)-1e-12)*10000)/10000
def main():
 es=read(P/'sports_open.json.gz');odds=read(P/'odds_snapshot.json');rows=[];audit=Counter();miss=[]
 for group in odds:
  sport=group['sport'];series=MAP.get(sport,'KXWTAMATCH' if sport.startswith('tennis_wta') else 'KXATPMATCH')
  for o in group.get('events',[]):
   start=ts(o['commence_time'])
   if start<group['fetched_ts']+300: audit['live_or_near_start_excluded']+=1;continue
   names=[o['home_team'],o['away_team']];matches=[]
   for e in es:
    if e['series_ticker']!=series:continue
    ms=[m for m in e['markets'] if m.get('status')=='active' and m.get('yes_sub_title') not in ('Tie','Draw')]
    if len(ms)!=2:continue
    mapped=[]
    for m in ms:
     ns=[n for n in names if matchname(m.get('yes_sub_title',''),n)]
     if len(ns)!=1:break
     mapped.append((m,ns[0]))
    if len(mapped)!=2 or len({n for m,n in mapped})!=2:continue
    # Time is checked against milestone below; broad occurrence gate avoids repeats.
    if any(abs(ts(m['occurrence_datetime'])-start)>4*3600 for m,n in mapped):continue
    matches.append((e,mapped))
   if len(matches)!=1:
    audit['unmatched_or_ambiguous']+=1;miss.append({'sport':sport,'home':names[0],'away':names[1],'matches':len(matches)});continue
   e,mapped=matches[0];audit['name_time_matched_events']+=1
   if series=='KXEPLGAME':
    for m in e['markets']:
     if m.get('yes_sub_title') in ('Tie','Draw'):mapped.append((m,'Draw'))
   allbooks={n:[] for m,n in mapped}
   for book in o.get('bookmakers',[]):
    for market in book.get('markets',[]):
     if market['key']!='h2h':continue
     last=ts(market.get('last_update') or book['last_update']);age=group['fetched_ts']-last
     if age<0 or age>300:continue
     outs=market['outcomes'];expected=3 if series=='KXEPLGAME' else 2
     if len(outs)!=expected or any(float(x['price'])<=1 for x in outs):continue
     implied={x['name']:1/float(x['price']) for x in outs};over=sum(implied.values())
     if set(implied)!=set(allbooks):continue
     for n in allbooks:
      prop=implied[n]/over;add=max(0,min(1,implied[n]-(over-1)/expected))
      allbooks[n].append({'book':book['key'],'proportional':prop,'additive':add,'quote_age':age,'updated':last})
   for m,n in mapped:
    books=allbooks[n]
    if len({b['book'] for b in books})<3:audit['insufficient_books_outcomes']+=1;continue
    mult=.5 if series=='KXMLBGAME' else 1
    # Initial catalog quote comparison; all positive rows need new books later.
    for side in ('yes','no'):
     ask=float(m.get(side+'_ask_dollars') or 1)
     if not 0<ask<1:continue
     fair=statistics.median(b['proportional'] if side=='yes' else 1-b['proportional'] for b in books)
     conservative=sorted(min(b['proportional'],b['additive']) if side=='yes' else min(1-b['proportional'],1-b['additive']) for b in books)[max(0,int(.1*len(books)))]
     rows.append({'sport':sport,'series':series,'event':e['event_ticker'],'ticker':m['ticker'],'side':side,'outcome':n,'home':o['home_team'],'away':o['away_team'],'odds_event':o['id'],'scheduled_start':start,'ask':ask,'fair':fair,'conservative_fair':conservative,'books':books,'fee_multiplier':mult,'edge_after_fee_1c':fair-ask-fee(ask,mult)/5-.01,'conservative_edge':conservative-ask-fee(ask,mult)/5-.01,'odds_capture':group['fetched_ts']})
 save(P/'sports_comparison.json',{'audit':dict(audit),'unmatched':miss,'rows':sorted(rows,key=lambda x:-x['edge_after_fee_1c'])})
 print(json.dumps({'audit':dict(audit),'outcomes_sides':len(rows),'positive':sum(x['edge_after_fee_1c']>0 for x in rows),'conservative_positive':sum(x['conservative_edge']>0 for x in rows),'best':[{k:x[k] for k in ['ticker','side','ask','fair','edge_after_fee_1c','conservative_edge']} for x in sorted(rows,key=lambda x:-x['edge_after_fee_1c'])[:12]]},indent=2))
if __name__=='__main__':main()
