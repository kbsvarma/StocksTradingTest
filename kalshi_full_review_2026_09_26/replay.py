"""Conservative causal quote-only baselines. No claim to model underlying value."""
import json,math,datetime,statistics,random,collections,hashlib,re
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;D=P/'candidate_data';O=P/'results';O.mkdir(exist_ok=True)
S={x['ticker']:x for x in json.loads((P/'data/series.json').read_text())['series']}
def ts(s):
 s=re.sub(r'\.(\d+)(?=Z|[+-]\d\d:)',lambda m:'.'+m.group(1).ljust(6,'0')[:6],s)
 return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def fee(p,m):return math.ceil((.07*m*2*p*(1-p)-1e-12)*10000)/10000
EXECUTION='close'
def q(c,side=None,execution=False):
 suffix='o' if execution and EXECUTION=='open' else 'c'
 try:b=float(c['b'+suffix]);a=float(c['a'+suffix])
 except (KeyError,TypeError,ValueError):return None
 if not 0<b<=a<1:return None
 side=side or ('yes' if a+b>=1 else 'no')
 return (side,b,a,(a+b)/2) if side=='yes' else (side,1-a,1-b,1-(a+b)/2)
def short(m,cs,kind,mult):
 close=ts(m['close_time']);by={c['t']:c for c in cs};start=ts(m['open_time'])
 if close-start>20*60 or close-start<10*60:return None
 if kind=='quote85':lo,hi,sp,left,right,obs=.85,.93,.02,3,10,0
 elif kind=='xrp80':lo,hi,sp,left,right,obs=.80,.90,.01,8,12,3
 elif kind=='zec95':lo,hi,sp,left,right,obs=.95,.99,.01,4,8,8
 else:lo,hi,sp,left,right,obs=.93,.99,.01,4,8,5
 for rem in range(right,left-1,-1):
  T=int(close-rem*60);c=by.get(T);v=q(c) if c else None
  if not v:continue
  side,b,a,mid=v
  price=a if kind=='quote85' else mid
  if not lo<=price<hi or a-b>sp+1e-8:continue
  if obs:
   hist=[q(by.get(int(close-i*60),{})) for i in range(14,rem-1,-1)]
   # Require complete path, not just occasional same-side quotes.
   if len(hist)<obs or not all(h and h[0]==side for h in hist):continue
  nxt=q(by.get(T+60,{}),side,execution=True)
  if not nxt:continue
  entry=max(a,nxt[2]);cap=min(a+.01,.939 if kind=='quote85' else .99)
  if entry>cap+1e-8:continue
  return trade(m,side,entry,T+(1 if EXECUTION=='open' else 60),mult,T)
def daily(m,cs,mult,weather=False):
 close=ts(m['close_time']);by={c['t']:c for c in cs}
 if weather:
  targets=range(45,14,-1);step=60;lo=.85;hi=.93;maxask=.939;hold=5
 else:targets=[8*60];step=3600;lo=.93;hi=.99;maxask=.99;hold=3
 for rem in targets:
  target=close-rem*60;available=[t for t in by if t<=target and target-t<=step]
  if not available:continue
  T=max(available);v=q(by[T]);
  if not v:continue
  side,b,a,mid=v
  if not lo<=a<hi or a-b>.02000001:continue
  hist=[q(by.get(T-i*step,{})) for i in range(hold)]
  if not all(h and h[0]==side for h in hist):continue
  nxt=q(by.get(T+step,{}),side,execution=True)
  if not nxt:continue
  entry=max(a,nxt[2])
  if entry>min(a+.01,maxask)+1e-8:continue
  return trade(m,side,entry,T+(1 if EXECUTION=='open' else step),mult,T)
def trade(m,side,entry,T,mult,signal):
 won=m['result']==side;cost=2*entry+fee(entry,mult);net=2*won-cost
 settle=ts(m['settlement_ts']) if m.get('settlement_ts') else None
 return {'ticker':m['ticker'],'event':m['event_ticker'],'side':side,'entry':entry,'fee':fee(entry,mult),'cost':cost,'won':won,'net':net,'stress1':net-.02,'stress2':net-.04,'signal_ts':signal,'entry_ts':T,'close_ts':ts(m['close_time']),'settle_ts':settle,'day':datetime.datetime.fromtimestamp(T,datetime.timezone.utc).date().isoformat()}
def stats(rs):
 if not rs:return {'trades':0}
 rs=sorted(rs,key=lambda r:(r['entry_ts'],r['ticker']));days=sorted({r['day'] for r in rs});cut=days[len(days)//2];by=collections.defaultdict(list)
 for r in rs:by[r['day']].append(r['stress1'])
 rng=random.Random(731);draw=[]
 for _ in range(5000):
  chosen=[rng.choice(days) for __ in days];s=sum(sum(by[d]) for d in chosen);n=sum(len(by[d]) for d in chosen);draw.append(s/n)
 draw.sort();equity=peak=dd=0
 for r in rs:equity+=r['stress1'];peak=max(peak,equity);dd=max(dd,peak-equity)
 return {'trades':len(rs),'wins':sum(r['won'] for r in rs),'losses':sum(not r['won'] for r in rs),'days':len(days),'net':round(sum(r['net'] for r in rs),4),'stress1':round(sum(r['stress1'] for r in rs),4),'stress2':round(sum(r['stress2'] for r in rs),4),'max_drawdown_stress1':round(dd,4),'first_half_stress1':round(sum(r['stress1'] for r in rs if r['day']<cut),4),'second_half_stress1':round(sum(r['stress1'] for r in rs if r['day']>=cut),4),'day_bootstrap_p05_per_trade_stress1':round(draw[250],5),'zero_loss_tail_unidentified':all(r['won'] for r in rs),'mean_entry':statistics.mean(r['entry'] for r in rs),'date_start':days[0],'date_end':days[-1]}
def portfolio(rs):
 # Explicit $100 start, $30 unavailable reserve, two contracts max, $4 daily and $10 high-water loss limits including pending risk.
 rs=sorted(rs,key=lambda r:(r['entry_ts'],r['ticker']));cash=100.;realized=0.;peak=0.;pending=[];kept=[];skips=collections.Counter();daily_net=collections.defaultdict(float)
 def day(t):return datetime.datetime.fromtimestamp(t,ZoneInfo('America/New_York')).date().isoformat()
 for r in rs:
  for old in list(pending):
   if old['settle_ts']<=r['entry_ts']:
    pending.remove(old);cash+=2*old['won'];realized+=old['stress1'];daily_net[day(old['settle_ts'])]+=old['stress1'];peak=max(peak,realized)
  if r['settle_ts'] is None or r['settle_ts']<r['entry_ts']:skips['missing_or_invalid_settlement_time']+=1;continue
  if pending:skips['unsettled_position']+=1;continue
  cost=r['cost']+.02
  if cost>2.02:skips['exposure_cap']+=1;continue
  if daily_net[day(r['entry_ts'])]-cost < -4:skips['daily_risk_budget']+=1;continue
  if peak-realized+cost>10:skips['drawdown_budget']+=1;continue
  if cash-cost<30:skips['reserve']+=1;continue
  cash-=cost;pending.append(r);kept.append(r)
 for r in pending:cash+=2*r['won']
 return {'ending_cash':round(cash,4),'accepted':len(kept),'skips':dict(skips),'metrics':stats(kept)}
def run():
 out=[]
 for mp in sorted(D.glob('*_markets.json')):
  t=mp.name.removesuffix('_markets.json');cp=D/(t+'_candles.jsonl')
  if not cp.exists():continue
  ms=json.loads(mp.read_text());candles={r['ticker']:r['candles'] for r in map(json.loads,cp.read_text().splitlines())};s=S.get(t,{})
  if s.get('fee_multiplier') is None:continue
  mult=float(s['fee_multiplier']);kindlist=['quote85','stable93'] if '15M' in t else ['weather85' if 'TEMP' in t else 'daily93']
  if t=='KXXRP15M':kindlist.append('xrp80')
  if t=='KXZEC15M':kindlist.append('zec95')
  for kind in kindlist:
   entries=[]
   for m in ms:
    cs=candles.get(m['ticker'],[])
    if not cs:continue
    r=short(m,cs,kind,mult) if '15M' in t else daily(m,cs,mult,weather=kind=='weather85')
    if r:entries.append(r)
   # Select by earliest signal then best price/ticker using observable values, never result.
   entries.sort(key=lambda r:(r['entry_ts'],r['entry'],r['ticker']));unique={}
   for r in entries:unique.setdefault(r['event'],r)
   entries=sorted(unique.values(),key=lambda r:r['entry_ts'])
   (O/(t+'_'+kind+'_'+EXECUTION+'_trades.json')).write_text(json.dumps(entries))
   record={'series':t,'rule':kind,'markets':len(ms),'markets_with_candles':sum(bool(candles.get(m['ticker'])) for m in ms),'fee_multiplier':mult,'unconstrained':stats(entries),'portfolio':portfolio(entries),'execution_proxy':EXECUTION,'source_model_included':False,'historical_depth_verified':False}
   out.append(record);print(t,kind,json.dumps(record['unconstrained']),flush=True)
 (O/('replay_results_'+EXECUTION+'.json')).write_text(json.dumps(out,indent=2))
if __name__=='__main__':
 import sys
 EXECUTION=sys.argv[1] if len(sys.argv)>1 else 'close'
 assert EXECUTION in ('open','close')
 run()
