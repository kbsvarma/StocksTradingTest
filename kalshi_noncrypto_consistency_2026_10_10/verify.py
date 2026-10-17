"""Independent chronology, ownership, cash and accounting audit of saved ledgers."""
import json,gzip,math,random,statistics,sys
from collections import defaultdict,Counter
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
def verify(kind):
 rs=json.load(open(P/(kind+'_results.json')));ll=json.load(gzip.open(P/(kind+'_ledgers.json.gz'),'rt'));errors=[];checks=0
 for key,rows in ll.items():
  stress=.02 if key.endswith('/cost2c') else .01;seen=set();active=[];cash=100.;real=defaultdict(float);eq=peak=dd=0
  for r in sorted(rows,key=lambda r:(r['settled'],r['event'])):
   eq+=r['net'];peak=max(peak,eq);dd=max(dd,peak-eq)
  if abs(eq-rs[key]['net'])>1e-4 or abs(dd-rs[key]['drawdown'])>1e-4:errors.append((key,'totals'))
  for r in sorted(rows,key=lambda r:(r['entered'],r.get('priority',0),r['asset'],r['event'],r['ticker'],r['side'])):
   checks+=1;err=[]
   for old in sorted(active,key=lambda r:(r['settled'],r['event'])):
    if old['settled']<=r['entered']:cash+=5*old['payout'];real[day(old['settled'])]+=old['net']
   active=[a for a in active if a['settled']>r['entered']]
   owner=(r['asset'],r['close']) if kind=='financial' else r['event']
   if owner in seen:err.append('duplicate')
   seen.add(owner)
   if not r['t']<r['entered']<r['close']<=r['settled']:err.append('chronology')
   if r['entry']>r['cap']+1e-8 or r['entry']<r['ask']-1e-8:err.append('cap_or_favorable_fill')
   if abs(r['net']-(5*(r['payout']-r['entry'])-r['fee']-5*stress))>1e-8:err.append('accounting')
   risk=sum(a['reserve'] for a in active)+r['reserve'];cost=5*r['entry']+r['fee']+5*stress
   if len(active)>=5 or risk>25+1e-8 or real[day(r['entered'])]-risk< -20-1e-8 or cash-cost<2-1e-8:err.append('risk')
   if kind=='financial':
    if not r['first_source_ts']<r['source_ts']<r['t']:err.append('source_lookahead')
    if any(a['asset']==r['asset'] for a in active):err.append('asset_overlap')
   elif r['entered']>=r['game_end']:err.append('after_game')
   if kind=='teachers' and (r['source_ts']>r['t'] or r['t']-r['source_ts']<r['delay']):err.append('teacher_source_lookahead')
   if r.get('calibration_boundary',r['t'])>r['t']:err.append('calibration_future')
   if err:errors.append((key,r['ticker'],err))
   active.append(r);cash-=cost
 print(kind,'ledger rows checked',checks,'errors',len(errors),flush=True)
 (P/(kind+'_verification.json')).write_text(json.dumps({'rows':checks,'scenarios':len(rs),'errors':errors},indent=2)+'\n')
 if errors:raise ValueError(errors[:5])

def diagnostics(kind):
 rs=json.load(open(P/(kind+'_results.json')));ll=json.load(gzip.open(P/(kind+'_ledgers.json.gz'),'rt'));out={};rng=random.Random(10102026)
 for k,r in rs.items():
  if not k.endswith('/base'):continue
  weeks=r['weekly'];ws=list(weeks.values());ds=list(r['daily'].values());rows=ll[k];bs=sorted(sum(rng.choice(ws) for _ in ws) for i in range(10000)) if ws else [0];by=defaultdict(float)
  for x in rows:by[x['asset']]+=x['net']
  costs={label:rs[k[:-4]+label]['net'] for label in ('base','cost2c','delay60','cap')}
  # Exact predeclared screen. Missing source coverage is an additional assessment.
  passed=(r['trades']>=100 and r['first_half']['net']>0 and r['second_half']['net']>0 and r['active_weeks']>=4 and r['positive_weeks']/r['active_weeks']>=.75 and min(costs.values())>0 and len([v for v in by.values() if v>0])>=2 and r['net']-max(by.values(),default=0)>0)
  out[k]={'passes_consistency_screen':passed,'scenario_nets':costs,'week_block_95_interval':[round(bs[int(.025*(len(bs)-1))],5),round(bs[int(.975*(len(bs)-1))],5)],'independence_warning':'Week resampling is descriptive, with few blocks; does not correct strategy selection or predict unseen regimes.','net_excluding_best_asset':round(r['net']-max(by.values(),default=0),5),'assets_net':dict(by),'first_half':r['first_half']['net'],'second_half':r['second_half']['net']}
 (P/(kind+'_diagnostics.json')).write_text(json.dumps(out,indent=2)+'\n')
if __name__=='__main__':
 for kind in sys.argv[1:] or ('sports','financial'):verify(kind);diagnostics(kind)
