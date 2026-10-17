from common import *
from portfolio import day
from decimal import Decimal as D,ROUND_CEILING
from collections import Counter,defaultdict

counts=Counter();errors=[]
def ck(ok,label):
 counts['assertions']+=1
 if not ok:errors.append(label)
def near(x,y):return abs(float(x)-float(y))<1e-7
for stem in ['operational','period','period_spread3','period_spread5','joint']:
 rr=read(P/(stem+'_ledgers.json.gz'));rs=read(P/(stem+'_results.json'))
 for key,rows in rr.items():
  stress=D('.02') if '/cost2c/' in '/'+key+'/' else D('.01');cash=D(100);active=[];real=defaultdict(lambda:D(0));seen=set();net_sum=D(0)
  if stem=='operational':spread=D('.03') if key.startswith('spread3') else D('.05')
  else:spread=D('.02') if stem=='period' else D('.03') if stem=='period_spread3' else D('.05')
  for r in rows:
   label=stem+'/'+key+'/'+r['event'];mult=D(str(r.get('fee_multiplier',1)))
   if r['asset'] in ('KXNHL1P','KXNHL2P','KXNHL3P'):mult=D(str(read(P/'period_data'/(r['asset']+'_series.json'))['fee_multiplier']))
   p=D(str(r['entry']));fee=(D('.07')*mult*5*p*(1-p)*10000).to_integral_value(rounding=ROUND_CEILING)/10000;cost=5*p+fee+5*stress;net=5*D(str(r['payout']))-cost
   ck(near(r['fee'],fee) and near(r['net'],net) and near(r['reserve'],cost),label+' accounting')
   ck(r['game_owner'] not in seen,label+' duplicate physical owner');seen.add(r['game_owner'])
   ck(r['score_wall']+r['source_delay']<=r['entered']<min(r['close'],r['settled'],r['game_end']) and r['score_age']<=120,label+' chronology')
   ck(r['ask']-r['bid']<=float(spread)+1e-8 and r['ask']<=r['entry']<=r['cap']+1e-8,label+' entry gate')
   if stem.startswith('period'):
    ck(r['side']=='yes' and r['period'] in [1,2,3] and r['minimum_lead']>=1 and 0<r['max_clock']<=300,label+' period strategy')
   for a,c,n in active:
    if a['settled']<=r['entered']:cash+=5*D(str(a['payout']));real[day(a['settled'])]+=n
   active=[x for x in active if x[0]['settled']>r['entered']];risk=sum((x[1] for x in active),D(0))+cost
   ck(len(active)<5 and risk<=25+D('1e-8') and cash-cost>=2-D('1e-8') and real[day(r['entered'])]-risk>=-20-D('1e-8'),label+' risk')
   cash-=cost;active.append((r,cost,net));net_sum+=net;counts['ledger_rows']+=1
  ck(near(net_sum,rs[key]['net']),stem+'/'+key+' net total');counts['scenarios']+=1
out={'passed':not errors,'counts':dict(counts),'errors':errors[:50],'limitations':['Accounting/chronology checks are not proof of source receipt times or actual fills.','Period score-versus-settlement identity was separately checked on all216 matched game-periods.']}
save(P/'audit_extra.json',out);print(json.dumps(out,indent=2));assert not errors
