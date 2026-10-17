from engine import *
from decimal import Decimal as Dec
import engine_initial

errors=[];counts=Counter();funds={};quotes={};execution=Counter()
def check(ok,msg):
 counts['assertions']+=1
 if not ok:errors.append(msg)
def near(a,b):return abs(float(a)-float(b))<1e-7
for file in sorted(P.glob('*_ledgers.json.gz')):
 if file.name.startswith(('pair','stress')):continue
 led=json.load(gzip.open(file,'rt'));result=json.load(open(P/file.name.replace('_ledgers.json.gz','_results.json')))
 for k,rows in led.items():
  if k.startswith('COMBINED100/'):continue
  counts['scenarios']+=1;capital=50 if '/capital50/' in k else 100;cash=float(capital);prev=-1
  slip=int(k.split('/slip')[1].split('/')[0]);delay=int(k.split('/delay')[1].split('/')[0]) if '/delay'in k else 3600 if file.name.startswith('hour') else 60
  lev=int(k.split('/L')[1].split('/')[0]) if '/L'in k else 2
  for r in rows:
   counts['trades']+=1;t=r['ticker'];q=Dec(str(r['qty']));side=r['side'];ep=Dec(str(r['price']));xp=Dec(str(r['exit_price']));fee=Dec(str(FEES[t]));label=file.name+'/'+k+'/'+str(r['entry'])
   if t not in funds:funds[t]=json.load(open(D/(t+'_funding.json')))['funding_rates']
   ff=-side*q*sum((Dec(str(f['funding_rate']))*Dec(f['mark_price']) for f in funds[t] if r['entry']<TS(f['funding_time'])<=r['exit']),Dec(0))
   gross=side*q*(xp-ep);fees=q*(ep+xp)*fee;slippage=q*(ep+xp)*Dec(slip)/10000;net=gross+ff-fees-slippage
   check(near(net,r['net']) and near(gross,r['gross']) and near(ff,r['funding']) and near(fees,r['fees']) and near(slippage,r['slippage']),label+' accounting')
   check(near(cash,r['capital']),label+' bankroll recursion')
   check(prev<=r['signal']<r['entry']<r['exit'],label+' chronology/overlap')
   check(r['entry']-r['signal']>=delay,label+' causal entry delay')
   check(isinstance(r['qty'],int) and r['qty']>=1,label+' integer size')
   check(r['estimated_risk']<=min(.02*capital,.02*cash)+1e-8,label+' per entry risk')
   check(r['day_loss']+r['estimated_risk']<=.06*capital+1e-8,label+' day risk gate')
   check(r['margin']<=.5*cash+1e-8 and near(r['margin'],float(q*ep)/lev),label+' margin')
   check(r['margin']+1e-8>=1.3*mr(t,side)*float(q*ep),label+' initial margin constraint')
   check(cash-r['margin']-float(q*ep*(fee+Dec(slip)/10000))>=.2*capital-1e-8,label+' free cash')
   cash+=r['net'];prev=r['exit']
  check(near(cash-capital,result[k]['net']),file.name+'/'+k+' net total')

# Independent checks of the two-leg branch, including funding on both legs.
pairled=json.load(gzip.open(P/'pair_ledgers.json.gz','rt'))
pairresults=json.load(open(P/'pair_results.json'))
for key,rows in pairled.items():
 cash=100.;prior=-1;slip=int(key.split('/slip')[1].split('/')[0])
 for r in rows:
  counts['pair_trades']+=1;total=0.;margin=0.;ec=0.
  check(near(cash,r['capital']) and prior<=r['signal']<r['entry']<r['exit'],key+' pair chronology/capital')
  for l in r['legs']:
   t=l['ticker'];q=Dec(str(l['qty']));ep=Dec(str(l['price']));xp=Dec(str(l['exit_price']));fee=Dec(str(FEES[t]))
   if t not in funds:funds[t]=json.load(open(D/(t+'_funding.json')))['funding_rates']
   ff=-l['side']*q*sum((Dec(str(f['funding_rate']))*Dec(f['mark_price']) for f in funds[t] if r['entry']<TS(f['funding_time'])<=r['exit']),Dec(0));net=l['side']*q*(xp-ep)+ff-q*(ep+xp)*(fee+Dec(slip)/10000)
   check(near(net,l['net']) and near(ff,l['funding']),key+' pair accounting');total+=float(net);margin+=l['margin'];ec+=float(q*ep*(fee+Dec(slip)/10000))
   check(isinstance(l['qty'],int) and l['qty']>=1 and l['margin']>=1.3*mr(t,l['side'])*float(q*ep)-1e-8,key+' pair size/margin')
  check(near(total,r['net']) and margin<=.5*cash+1e-8 and cash-margin-ec>=20-1e-8,key+' pair combined capital')
  check(r['estimated_risk']<=min(2,.02*cash)+1e-8 and r['estimated_risk']+r['day_loss']<=6+1e-8,key+' pair risk')
  cash+=total;prior=r['exit']
 check(near(cash-100,pairresults[key]['net']),key+' pair total')

# Combined adverse-bar stress is audited per fixed $50 partition, never as
# two separately funded $100 accounts.
stressled=json.load(gzip.open(P/'stress_ledgers.json.gz','rt'));stressresults=json.load(open(P/'stress_results.json'))
for key,rows in stressled.items():
 cash=Counter({t:50. for t in ['KXSILVERPERP','KXPALLADIUMPERP']});prior=Counter();slip=int(key.split('/slip')[1].split('/')[0])
 for r in rows:
  counts['stress_trades']+=1;t=r['ticker'];q=Dec(str(r['qty']));ep=Dec(str(r['price']));xp=Dec(str(r['exit_price']));fee=Dec(str(FEES[t]));ff=-r['side']*q*sum((Dec(str(f['funding_rate']))*Dec(f['mark_price']) for f in funds[t] if r['entry']<TS(f['funding_time'])<=r['exit']),Dec(0));net=r['side']*q*(xp-ep)+ff-q*(ep+xp)*(fee+Dec(slip)/10000)
  check(near(cash[t],r['capital']) and prior[t]<=r['signal']<r['entry']<r['exit'],key+' stress chronology/capital')
  check(near(net,r['net']) and near(ff,r['funding']),key+' stress accounting')
  check(r['margin']<=.5*cash[t]+1e-8 and cash[t]-r['margin']-float(q*ep*(fee+Dec(slip)/10000))>=10-1e-8,key+' stress capital')
  check(r['estimated_risk']<=min(1,.02*cash[t])+1e-8 and r['estimated_risk']+r['day_loss']<=3+1e-8,key+' stress risk')
  cash[t]+=float(net);prior[t]=r['exit']
 check(near(sum(cash.values())-100,stressresults[key]['net']),key+' stress total')

# Existing defaults must reproduce the preserved initial implementation.
t='KXSILVERPERP';h,_=load(t,3600);q,f=load(t,60);i=indicators(t,h,3600,'hour')
a=run(t,'h_reversal24',q,f,i,60,START,END,signal_Q=h);b=engine_initial.run(t,'h_reversal24',q,f,i,60,START,END,signal_Q=h)
check(a==b,'default engine parity')
# Truncating future data must not change historical indicators or signals.
for t,rule,period,kind in [('KXSILVERPERP','h_reversal24',3600,'hour'),('KXPALLADIUMPERP','m_reversal240',60,'minute'),('KXDOGEPERP','h_ema8_32',3600,'hour')]:
 q,_=load(t,period);cut=sorted(q)[len(q)//2];truncated={s:v for s,v in q.items() if s<=cut};full=indicators(t,q,period,kind);short=indicators(t,truncated,period,kind)
 for s in list(short)[::17]:
  check(short[s]==full[s],t+' future invariant indicator '+str(s));check(signal(t,rule,s,q,full)==signal(t,rule,s,truncated,short),t+' future invariant signal '+str(s))

# Trading-volume diagnostics are not order-book depth validation.
led=json.load(gzip.open(P/'portfolio_ledgers.json.gz','rt'))
selected=led['COMBINED100/full/L2/slip1/delay60/revalidate1'];diag=[]
for r in selected:
 t=r['ticker']
 if t not in quotes:quotes[t]=load(t,60)[0]
 q=quotes[t][r['entry']];diag.append({'ticker':t,'entry':r['entry'],'qty':r['qty'],'minute_volume':q.vol,'spread_bps':(q.ask-q.bid)/q.mid*10000,'notional':r['qty']*r['price']})
counts['selected_entries_zero_minute_volume']=sum(x['minute_volume']==0 for x in diag)
counts['selected_entries_qty_exceeds_minute_volume']=sum(x['qty']>x['minute_volume'] for x in diag)
out={'passed':not errors,'counts':dict(counts),'errors':errors[:50],'selected_execution_diagnostics':diag,'limitations':['Historical executable depth and queue not available; minute volume is diagnostic only.','Current fee and margin configuration applied retrospectively; not a historical risk-parameter replay.','Minute-close stops can miss intraminute events.']}
save('audit_results.json',out);print(json.dumps({k:v for k,v in out.items() if k!='selected_execution_diagnostics'},indent=2));assert not errors
