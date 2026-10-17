from sports_compare import *
from datetime import datetime,timezone
import sys,requests
ROOT=P.parent
sys.path.insert(0,str(ROOT/'kalshi_three_lane_research_2026_08'))
import ast,os
source=ast.parse((ROOT/'kalshi_three_lane_research_2026_08/sportsbook_forward_snapshot.py').read_text())
fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='load_api_key')
exec(compile(ast.Module(body=[fn],type_ignores=[]),'key_loader','exec'))
api=load_api_key()
# Three current candidate pairs, highest apparent h2h edges. No order routes.
old=read(P/'sports_comparison.json')['rows'];best=[];seen=set()
for r in old:
 if r['event'] not in seen:best.append(r);seen.add(r['event'])
 if len(best)>=3:break
out=[]
for r in best:
 response=requests.get('https://api.the-odds-api.com/v4/sports/'+r['sport']+'/events/'+r['odds_event']+'/odds',params={'apiKey':api,'regions':'us','markets':'h2h','oddsFormat':'decimal'},timeout=25)
 if response.status_code!=200:
  out.append({'event':r['event'],'error_status':response.status_code});continue
 obj=response.json();stamp=time.time();books=[]
 for b in obj.get('bookmakers',[]):
  for m in b.get('markets',[]):
   if m['key']!='h2h':continue
   age=stamp-ts(m.get('last_update') or b['last_update'])
   if not 0<=age<=300 or len(m['outcomes'])!=(3 if r['series']=='KXEPLGAME' else 2):continue
   imp={v['name']:1/float(v['price']) for v in m['outcomes']}
   if r['outcome'] not in imp:continue
   p=imp[r['outcome']]/sum(imp.values());books.append({'book':b['key'],'p':p if r['side']=='yes' else 1-p,'age':age})
 market=get('/markets/'+r['ticker'],cache=False)['market']
 book=get('/markets/'+r['ticker']+'/orderbook',{'depth':100},False)
 miles=get('/milestones',{'related_event_ticker':r['event'],'limit':500},False)
 fixed=book.get('orderbook_fp',{});opp='no_dollars' if r['side']=='yes' else 'yes_dollars';levels=sorted([(1-float(a),float(n)) for a,n in fixed.get(opp,[])],key=lambda x:x[0]);left=5;cost=0;used=[]
 for ask,n in levels:
  take=min(n,left);cost+=ask*take;used.append([ask,take]);left-=take
  if left<=0:break
 fair=statistics.median(b['p'] for b in books) if len(books)>=3 else None
 avg=cost/5 if left<=0 else None
 # Conservatively calculate each price tier separately for fee rounding.
 fee_sum=sum(math.ceil((.07*r['fee_multiplier']*n*a*(1-a)-1e-12)*10000)/10000 for a,n in used)
 matching=[x for x in miles.get('milestones',[]) if (x.get('details') or {}).get('main_game_event_ticker')==r['event']]
 out.append({'event':r['event'],'ticker':r['ticker'],'side':r['side'],'old_edge':r['edge_after_fee_1c'],'current_fair':fair,'five_contract_avg':avg,'edge_after_fee1c':fair-avg-fee_sum/5-.01 if fair is not None and avg is not None else None,'books':books,'levels_used':used,'market':market,'book':book,'milestones':miles,'exact_milestone_start_matches':len(matching)==1 and ts(matching[0]['start_date'])==r['scheduled_start'],'odds':obj,'captured_ts':stamp,'credits_remaining':response.headers.get('x-requests-remaining')})
save(P/'sports_recheck.json',out)
print(json.dumps([{k:v for k,v in r.items() if k in ('ticker','side','old_edge','current_fair','five_contract_avg','edge_after_fee1c','exact_milestone_start_matches','credits_remaining')} for r in out],indent=2))
