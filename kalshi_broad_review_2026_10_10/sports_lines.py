import statistics,math,json,time
from collections import defaultdict,Counter
from common import *
from sports_compare import MAP,matchname,fee
def main():
 lines=read(P/'sports_lines_open.json.gz');data=read(P/'odds_lines_snapshot.json');base=read(P/'sports_comparison.json')
 identities={(r['sport'],r['odds_event']):r['event'] for r in base['rows']}
 byevent={e['event_ticker']:e for e in lines};rows=[];audit=Counter()
 for group in data:
  sport=group['sport'];gs=MAP[sport]
  for o in group.get('events',[]):
   e=identities.get((sport,o['id']))
   if not e:continue
   start=ts(o['commence_time'])
   if start<=group['fetched_ts']+300:continue
   for kind,suffix in [('spreads','SPREAD'),('totals','TOTAL')]:
    event=byevent.get(e.replace(gs,gs.replace('GAME',suffix),1))
    if not event:continue
    for m in event['markets']:
     line=m.get('floor_strike')
     if m.get('status')!='active' or line is None:continue
     line=float(line)
     if abs(line-round(line))<1e-8:audit['integer_line_push_omitted']+=1;continue
     team=m.get('yes_sub_title','').split(' wins by')[0] if kind=='spreads' else None
     books=[]
     for b in o.get('bookmakers',[]):
      for q in b.get('markets',[]):
       if q['key']!=kind:continue
       updated=ts(q.get('last_update') or b['last_update']);age=group['fetched_ts']-updated
       if not 0<=age<=300 or len(q['outcomes'])!=2:continue
       out=q['outcomes'];yes=[v for v in out if (v['name']=='Over' and abs(float(v.get('point',999))-line)<1e-8) if kind=='totals'] if kind=='totals' else [v for v in out if matchname(team,v['name']) and abs(float(v.get('point',999))+line)<1e-8]
       if len(yes)!=1:continue
       other=[v for v in out if v is not yes[0]][0]
       # Require an exactly opposite half-point line, not adjacent or alt totals.
       if kind=='spreads' and abs(float(other['point'])-line)>1e-8:continue
       if kind=='totals' and abs(float(other['point'])-line)>1e-8:continue
       a=1/float(yes[0]['price']);bb=1/float(other['price']);over=a+bb
       books.append({'book':b['key'],'proportional':a/over,'additive':max(0,min(1,a-(over-1)/2)),'updated':updated,'quote_age':age})
     if len({b['book'] for b in books})<3:continue
     audit['matched_lines']+=1
     for side in ['yes','no']:
      ask=float(m.get(side+'_ask_dollars') or 1)
      if not 0<ask<1:continue
      fair=statistics.median(b['proportional'] if side=='yes' else 1-b['proportional'] for b in books)
      conservative=sorted(min(b['proportional'],b['additive']) if side=='yes' else min(1-b['proportional'],1-b['additive']) for b in books)[int(.1*len(books))]
      rows.append({'ticker':m['ticker'],'series':event['series_ticker'],'sport':sport,'odds_event':o['id'],'event':event['event_ticker'],'base_event':e,'side':side,'ask':ask,'fair':fair,'conservative_fair':conservative,'edge_after_fee_1c':fair-ask-fee(ask,1)/5-.01,'conservative_edge':conservative-ask-fee(ask,1)/5-.01,'books':books,'odds_capture':group['fetched_ts'],'scheduled_start':start,'line':line,'team':team,'kind':kind,'rules':m['rules_primary']})
 save(P/'sports_lines_comparison.json',{'audit':dict(audit),'rows':sorted(rows,key=lambda x:-x['edge_after_fee_1c'])})
 print(json.dumps({'audit':dict(audit),'rows':len(rows),'positive':sum(r['edge_after_fee_1c']>0 for r in rows),'conservative_positive':sum(r['conservative_edge']>0 for r in rows),'top':[{k:r[k] for k in ['ticker','side','ask','fair','edge_after_fee_1c','conservative_edge']} for r in sorted(rows,key=lambda x:-x['edge_after_fee_1c'])[:12]]},indent=2))
if __name__=='__main__':main()
