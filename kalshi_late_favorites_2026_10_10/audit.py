from common import *
from late import parse_states,fee_multiplier_at
from portfolio import quote,payout,day,metrics
from decimal import Decimal as Dec,ROUND_CEILING
from collections import Counter,defaultdict
from pathlib import Path
import bisect,math

errors=[];counts=Counter();index={};byevent=defaultdict(list)
def ck(ok,msg):
 counts['assertions']+=1
 if not ok:errors.append(msg)
def near(x,y):return abs(float(x)-float(y))<1e-7

for folder in ['data','earlier']:
 for d in (P/folder).iterdir() if (P/folder).exists() else []:
  if not (d/'markets.json').exists():continue
  for m in read(d/'markets.json'):index[m['ticker']]=(d,m)
for stem in ['','extended_']:
 lp=P/(stem+'ledgers.json.gz');rp=P/(stem+'results.json')
 if not lp.exists():continue
 led=read(lp);results=read(rp)
 for key,rows in led.items():
  counts['scenarios']+=1;stress=Dec('.02') if key.endswith('/cost2c') else Dec('.01');cash=Dec(100);active=[];real=defaultdict(lambda:Dec(0));seen=set()
  for r in sorted(rows,key=lambda x:(x['entered'],x['asset'],x['event'],x['ticker'],x['side'])):
   counts['ledger_rows']+=1;label=stem+key+'/'+r['event'];p=Dec(str(r['entry']));mult=Dec(str(r['fee_multiplier']));fee=(Dec('.07')*mult*5*p*(1-p)*10000).to_integral_value(rounding=ROUND_CEILING)/10000;net=5*(Dec(str(r['payout']))-p)-fee-5*stress;cost=5*p+fee+5*stress
   ck(near(fee,r['fee']) and near(net,r['net']) and near(cost,r['reserve']),label+' accounting')
   ck(r['event'] not in seen,label+' duplicate game');seen.add(r['event'])
   ck(r['score_wall']+r['source_delay']<=r['entered']<min(r['game_end'],r['close'],r['settled']),label+' causal timestamps')
   ck(r['score_age']<= (180 if r['asset']=='KXMLBGAME' else 120),label+' stale state')
   diff=r['home_score']-r['away_score'];ck(diff!=0 and r['team']==(r['home_team'] if diff>0 else r['away_team']),label+' leader')
   if r['profile'].startswith('original_favorite'):ck(r['team']==r['pregame_favorite'],label+' pregame favorite')
   low,high=(.9,.95) if r['profile'].endswith('p90') else(.95,.98)
   ck(low<=r['ask']<high and r['ask']-r['bid']<=.02000001 and r['ask']<=r['entry']<=r['cap']+1e-8,label+' price gates')
   for old in active:
    if old['settled']<=r['entered']:cash+=5*Dec(str(old['payout']));real[day(old['settled'])]+=Dec(str(old['net']))
   active=[a for a in active if a['settled']>r['entered']];risk=sum((Dec(str(a['reserve'])) for a in active),Dec(0))+cost
   ck(len(active)<5 and risk<=25+Dec('1e-8') and real[day(r['entered'])]-risk>=-20-Dec('1e-8') and cash-cost>=2-Dec('1e-8'),label+' portfolio risk')
   cash-=cost;active.append(r);byevent[r['event']].append((key,r))
  cash+=sum((5*Dec(str(a['payout'])) for a in active),Dec(0));ck(near(cash-100,results[key]['net']),stem+key+' portfolio total')

# Inspect raw quotes, pregame favorite, target IDs and individual play states.
# Per-event loading avoids keeping the large archive in memory.
for et,entries in byevent.items():
 d,m0=index[entries[0][1]['ticker']];mile=read(d/(et+'_milestone.json'));cm=read(d/(et+'_candles.json.gz'));cs={t:{c['end_period_ts']:c for c in rows} for t,rows in cm.items()};pp=read(d/(et+'_plays.json.gz'));ep=d/(et+'_espn.json.gz');s=entries[0][1]['asset'];states,end,cov=parse_states(pp,mile,s,read(ep) if ep.exists() else None);stamps=[r['wall'] for r in states];pre=(ts(mile['start_date'])-1800)//60*60
 unique={}
 for key,r in entries:unique[(r['ticker'],r['side'],r['t'],r['entered'],r['source_delay'])]=(key,r)
 for key,r in unique.values():
  counts['unique_decisions_checked']+=1;label=et+'/'+str(r['t']);m=index[r['ticker']][1];q=quote(cs[r['ticker']].get(r['t']),r['side']);phase='close' if key.endswith(('/delay60','/both_delays')) else 'open';q2=quote(cs[r['ticker']].get(r['t']+60),r['side'],phase)
  ck(q is not None and near(q[0],r['bid']) and near(q[1],r['ask']),label+' decision quote')
  ck(q2 is not None and near(max(q[1],q2[1]),r['entry']),label+' execution quote')
  ck(near(payout(m,r['side']),r['payout']),label+' settlement')
  ck(near(fee_multiplier_at(s,r['entered'],r['fee_multiplier']),r['fee_multiplier']),label+' historical fee schedule')
  j=bisect.bisect_right(stamps,r['entered']-r['source_delay'])-1;state=states[j] if j>=0 else None
  ck(state is not None and state['wall']==r['score_wall'] and state['home']==r['home_score'] and state['away']==r['away_score'] and state['period']==r['period'] and not state['terminal'],label+' actual observed play')
  if r['profile'].startswith('original_favorite'):
   own=[t for t in cs if r['team'] in (index[t][1].get('custom_strike') or {}).values()];qpre=quote(cs[own[0]].get(pre)) if len(own)==1 else None
   ck(qpre is not None and sum(qpre)/2>=.6 and qpre[1]-qpre[0]<=.02000001,label+' pregame price')
  ck(sum(float(cs[r['ticker']].get(t,{}).get('volume_fp') or 0) for t in range(r['t']-540,r['t']+60,60))>=5,label+' observed prior volume')

out={'passed':not errors,'counts':dict(counts),'errors':errors[:60],'unique_games_checked':len(byevent),'limits':['Repeated scenarios are not independent observations.','Official play timestamps are not all verified local receipt timestamps.','Candle execution is not proof of displayed five-contract depth.']};save(P/'audit_results.json',out);print(json.dumps(out,indent=2));assert not errors
