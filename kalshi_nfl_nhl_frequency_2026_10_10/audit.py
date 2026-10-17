"""Independent accounting, data identity and source/quote checks."""
from study import *
from decimal import Decimal as D, ROUND_CEILING

counts=Counter();errors=[]
def ck(ok,why):
 counts['assertions']+=1
 if not ok:errors.append(why)
def near(x,y):return abs(float(x)-float(y))<1e-7
cand=read(P/'candidates.json.gz');index={x['candidate_id']:x for rr in cand.values() for x in rr};res=read(P/'results.json');led=read(P/'ledgers.json.gz')
bygame=defaultdict(list)
for c in index.values():bygame[c['event']].append(c)
for s in FAMILIES:
 d=P/'data'/s;groups=defaultdict(list)
 for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
 for et,ms in groups.items():
  mi=read(d/(et+'_milestone.json'));cm=read(d/(et+'_candles.json.gz'));y=read(d/(et+'_espn.json.gz'))
  states,stamps,end,home,away,tm,cs,pm,pe,pa,comp,cov=prepare(ms,cm,mi,s,y);mindex={m['ticker']:m for m in ms}
  # Final header scores are used only for outcome/source identity audit.
  parts=(y.get('header') or {}).get('competitions',[{}])[0].get('competitors',[])
  scores={p['homeAway']:int(p['score']) for p in parts if str(p.get('score','')).isdigit()}
  completed=(y.get('header') or {}).get('competitions',[{}])[0].get('status',{}).get('type',{}).get('completed',False)
  if len(scores)==2 and completed:
   for m in ms:
    hp=.5 if scores['home']==scores['away'] else int(scores['home']>scores['away']);expected=hp if tm[m['ticker']]==home else 1-hp
    ck(near(payout(m,'yes'),expected),et+' final score / settlement mismatch')
  if not completed:
   counts['noncompleted_or_refunded_events']+=1
   ck(all(m.get('result')=='scalar' for m in ms) and near(sum(payout(m,'yes') for m in ms),1),et+' noncompleted settlement')
  counts['games']+=1
  for c in bygame[et]:
   counts['candidates']+=1;lab=c['candidate_id'];q=quote(cs[c['ticker']].get(c['t']),c['side']);mode='close' if '/delay60/' in lab or '/both_delays/' in lab else 'open';q2=quote(cs[c['ticker']].get(c['t']+60),c['side'],mode)
   ck(q is not None and near(q[0],c['bid']) and near(q[1],c['ask']),lab+' decision quote')
   ck(q2 is not None and near(max(q[1],q2[1]),c['entry']),lab+' next quote')
   ck(c['score_wall']+c['source_delay']<=c['entered']<min(c['game_end'],c['close'],c['settled']),lab+' chronology')
   state=at(states,stamps,c['entered'],c['source_delay'])
   ck(state is not None and state['wall']==c['score_wall'] and state['home']==c['home_score'] and state['away']==c['away_score'] and not state['terminal'],lab+' raw play')
   ck(c['score_age']<=120 and c['ask']<=c['entry']<=c['cap']+1e-8,lab+' gates')
   ck(near(payout(mindex[c['ticker']],c['side']),c['payout']),lab+' payout')
   if c['side']=='no':ck(comp,lab+' complement')
   ck(c['pregame_mid']==pm.get(c['team']),lab+' pregame quote')
   ck(sum(float(cs[c['ticker']].get(t,{}).get('volume_fp') or cs[c['ticker']].get(t,{}).get('volume') or 0) for t in range(c['t']-540,c['t']+60,60))>=5,lab+' prior volume')

for key,rr in led.items():
 counts['scenarios']+=1;s,f,m,band,label,part=key.split('/');friction=D('.02') if label=='cost2c' else D('.01');cash=D(100);real=defaultdict(lambda:D(0));active=[];seen=set();total=D(0)
 for tiny in rr:
  c=index[tiny['candidate_id']];lab=key+'/'+c['event'];p=D(str(c['entry']));mf=D(str(c['fee_multiplier']));fval=(D('.07')*mf*5*p*(1-p)*10000).to_integral_value(rounding=ROUND_CEILING)/10000;cost=5*p+fval+5*friction;net=5*D(str(c['payout']))-cost
  ck(near(c['fee'],fval) and near(net,tiny['net']) and near(cost,tiny['reserve']),lab+' independent accounting')
  ck(accepts(c,f,int(m[1:])) and period(c,part,s),lab+' profile/period')
  ck(c['game_owner'] not in seen,lab+' duplicate physical game');seen.add(c['game_owner'])
  for a,oldcost,oldnet in active:
   if a['settled']<=c['entered']:cash+=5*D(str(a['payout']));real[day(a['settled'])]+=oldnet
  active=[v for v in active if v[0]['settled']>c['entered']];risk=sum((v[1] for v in active),D(0))+cost
  ck(len(active)<5 and risk<=D(25)+D('1e-8') and real[day(c['entered'])]-risk>=-20-D('1e-8') and cash-cost>=2-D('1e-8'),lab+' risk')
  cash-=cost;active.append((c,cost,net));total+=net;counts['ledger_rows']+=1
 ck(near(total,res[key]['net']),key+' result total')
out={'passed':not errors,'counts':dict(counts),'errors':errors[:100],'limits':['Historical source revisions/arrival latency not independently reconstructable.','Quote candles and prior volume do not establish executable five-contract depth.','Repeated scenarios are not independent trade observations.']}
save(P/'audit.json',out);print(json.dumps(out,indent=2));assert not errors
