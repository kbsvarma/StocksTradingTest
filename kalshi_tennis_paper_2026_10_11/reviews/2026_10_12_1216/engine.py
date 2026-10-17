"""Frozen tennis signal and conservative paper book execution. No networking."""
import math,json,hashlib,datetime as dt
from decimal import Decimal,ROUND_CEILING
from zoneinfo import ZoneInfo
from model import Model,fit
ET=ZoneInfo('America/New_York')
def ts(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def fee(p,m=1):
 return float((Decimal('.07')*Decimal(str(m))*Decimal(str(p))*(1-Decimal(str(p)))).quantize(Decimal('.0001'),rounding=ROUND_CEILING))
def quote(c,side='yes'):
 if not c:return None
 try:
  b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
 except (ValueError,TypeError,KeyError):return None
 if not math.isfinite(b+a) or not 0<=b<=a<=1:return None
 return (b,a) if side=='yes' else (round(1-a,4),round(1-b,4))
def plays(payload):
 out=[]
 for per in payload.get('pbp',{}).get('periods',[]):
  for e in per.get('events',[]):
   if e.get('wall_clock'):out.append({**e,'home':per.get('home_competitor_id'),'away':per.get('away_competitor_id')})
 return sorted(out,key=lambda x:(int(x['wall_clock']),x.get('event_type')=='game_won'))
def point_key(e):return canonical([e.get(k) for k in ('wall_clock','event_type','server','home','away')])
def probability(items,prior,boundary,cfg):
 eligible=[e for e in items if float(e['wall_clock'])<=boundary-cfg['score_lag_seconds']]
 if not eligible:return None,'no_90s_score'
 e=eligible[-1];home,away=prior['home'],prior['away']
 if e.get('event_type')!='point':return None,'latest_state_not_point'
 if boundary-float(e['wall_clock'])>cfg['max_score_age_seconds']:return None,'score_older_than_120s'
 if (e['home'],e['away'])!=(home,away):return None,'competitor_mapping'
 try:
  sa,sb,ga,gb=[int(e[k]) for k in ('home_sets','away_sets','home_games','away_games')];sc={0:0,15:1,30:2,40:3,50:4};a=sc[int(e['home_score'])];b=sc[int(e['away_score'])];s=[home,away].index(e['server'])
 except (KeyError,ValueError,TypeError):return None,'invalid_score'
 if sa not in (0,1) or sb not in (0,1) or min(ga,gb)<0 or max(ga,gb)>6 or ga==gb==6 or (max(ga,gb)>=6 and abs(ga-gb)>=2) or (max(a,b)>=4 and abs(a-b)>=2):return None,'terminal_or_tiebreak'
 counts=[0,0,0,0];seen=set()
 for r in eligible:
  ident=(r['wall_clock'],r.get('server'),r.get('result'))
  if ident in seen or r.get('event_type') not in ('point','game_won') or r.get('server') not in (home,away) or r.get('result') not in ('server_won','receiver_won'):continue
  seen.add(ident);i=0 if r['server']==home else 2;counts[i]+=r['result']=='server_won';counts[i+1]+=1
 n=cfg['serve_prior'];pa=round((n*prior['pa']+counts[0])/(n+counts[1]),3);pb=round((n*prior['pb']+counts[2])/(n+counts[3]),3)
 model=Model(pa,pb);p=model.current(sa,sb,ga,gb,a,b,s);model.clear()
 return dict(probability_home=p,source_t=e['wall_clock'],score=[sa,sb,ga,gb,a,b,s],serve_counts=counts,serve_prob=[pa,pb]),None
def choose(markets,candles,pstate,prior,boundary,mult,cfg):
 ranked=[]
 for m in markets:
  player=m.get('custom_strike',{}).get('tennis_competitor')
  if player not in (prior['home'],prior['away']):continue
  cs=candles.get(m['ticker'],{});volume=sum(float(cs.get(t,{}).get('volume_fp') or 0) for t in range(boundary-540,boundary+1,60))
  if volume<cfg['min_volume_10m']:continue
  for side in ('yes','no'):
   q=quote(cs.get(boundary),side)
   if not q or not cfg['min_ask']<=q[1]<=cfg['max_ask'] or q[1]-q[0]>cfg['max_spread']+1e-8:continue
   p=pstate['probability_home'] if (player==prior['home'])==(side=='yes') else 1-pstate['probability_home'];pred=(1-cfg['blend'])*p+cfg['blend']*sum(q)/2;edge=pred-q[1]-fee(q[1],mult)-cfg['base_friction']
   ranked.append(dict(ticker=m['ticker'],side=side,bid=q[0],ask=q[1],prediction=pred,raw_probability=p,edge=edge,volume10=volume,mult=mult,cap=round(min(cfg['max_cap'],q[1]+cfg['cap_increment']),4),boundary=boundary,**pstate))
 if not ranked:return None,'quote_price_spread_or_volume'
 best=max(ranked,key=lambda r:(r['edge'],r['ticker'],r['side']))
 return (best,None) if best['edge']+1e-10>=cfg['edge'] else (None,'edge_below_6c')
def book_fill(payload,side,cap,qty=1):
 """Opposite bids are asks; aggregate exact fixed-point fractional depth."""
 ob=payload.get('orderbook_fp')
 if not isinstance(ob,dict):return None,'missing_fixed_point_book'
 try:
  own=[(float(p),float(q)) for p,q in ob.get(side+'_dollars',[])];opp=[(1-float(p),float(q)) for p,q in ob.get(('no' if side=='yes' else 'yes')+'_dollars',[])]
  if any(not math.isfinite(p+q) or not 0<=p<=1 or q<0 for p,q in own+opp):return None,'invalid_book'
 except (ValueError,TypeError):return None,'invalid_book'
 asks=sorted((round(p,4),q) for p,q in opp if q>0);bid=max((p for p,q in own if q>0),default=0)
 if asks and bid>asks[0][0]+1e-8:return None,'crossed_book'
 remaining=qty;cost=0.;levels=[];available=0.
 for p,q in asks:
  if p>cap+1e-8:break
  available+=q;take=min(remaining,q)
  if take>0:levels.append([p,take]);cost+=p*take;remaining-=take
 if remaining>1e-8:return None,'insufficient_depth_inside_cap'
 return dict(vwap=cost/qty,levels=levels,available=available,best_bid=bid,best_ask=asks[0][0]),None
def payout(m,side):
 if not m.get('settlement_ts'):return None
 if m.get('result') in ('yes','no'):return float(m['result']==side)
 try:
  v=float(m['settlement_value_dollars'])
  return (v if side=='yes' else 1-v) if 0<=v<=1 else None
 except (KeyError,ValueError,TypeError):return None
