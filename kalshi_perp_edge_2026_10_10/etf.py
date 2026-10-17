from core import *
from zoneinfo import ZoneInfo
from collections import defaultdict
ET=ZoneInfo('America/New_York');r=json.load(open(D/'ibit_chart.json'))['chart']['result'][0];px=r['indicators']['adjclose'][0]['adjclose'];cl=r['indicators']['quote'][0]['close'];ETF={}
for t,p,c in zip(r['timestamp'],px,cl):
 if p is None:continue
 date=dt.datetime.fromtimestamp(t,ET).date();stamp=int(dt.datetime.combine(date,dt.time(16),ET).timestamp());ETF[stamp]=float(p)
rows=[];paths={};CAP=10000.;t='KXBTCPERP';CLOSES=sorted(s for s in ETF if at(t,s) and s<END)
def simulate(start,L,slip,hedge_mult=1):
 s=next(s for s in CLOSES if s>=start);end=CLOSES[-1];p=ETF[s];q=at(t,s);n_target=CAP/(1+1/L);sh=int(n_target/p);n=int(round(sh*p/q.mid*hedge_mult));entry=q.bid;margin=n*entry/L;entryfee=n*entry*.0012;etfcost=sh*p*(1+slip/10000);perpslip=n*entry*.0001;idle=CAP-etfcost-margin-entryfee-perpslip
 while idle<0:
  sh-=1;n=int(round(sh*p/q.mid*hedge_mult));margin=n*entry/L;entryfee=n*entry*.0012;etfcost=sh*p*(1+slip/10000);perpslip=n*entry*.0001;idle=CAP-etfcost-margin-entryfee-perpslip
 fund=0.;pending=False;perp_exit=None;exitperp=None;mincoll=1e99;maxratio=0;intsum=0;nd=0;path=[];breach=None
 for stamp in range(s+60,end+1,60):
  if perp_exit is None and stamp in F[t]:
   rr,mk=F[t][stamp];fund+=n*rr*mk
  v=at(t,stamp)
  if v is None:continue
  if perp_exit is None:
   eq=margin+fund+n*(entry-v.ask);mincoll=min(mincoll,eq);ratio=mrate(t,-1)*n*v.mid/max(eq,1e-9);maxratio=max(maxratio,ratio);intsum+=max(0,eq)*.0325*60/(365*86400)
   if pending:perp_exit=stamp;exitperp=v.ask
   elif ratio>=.85:pending=True;breach=stamp
  if stamp in ETF:
   exitprice=exitperp if perp_exit is not None else v.ask
   cashperp=margin+fund+n*(entry-exitprice)-n*exitprice*(.0012+.0001)
   equity=idle+cashperp+sh*ETF[stamp]*(1-slip/10000)-sh*.01
   path.append({'t':stamp,'equity':equity})
   if perp_exit is not None:end=stamp;break
 if perp_exit is None:perp_exit=end;exitperp=at(t,end).ask
 net=path[-1]['equity']-CAP;grossperp=n*(entry-exitperp);grossetf=sh*(ETF[end]-p);fees=entryfee+n*exitperp*.0012+sh*.01;cost=perpslip+n*exitperp*.0001+sh*(p+ETF[end])*slip/10000
 assert abs(fund-n*sum(rr*mk for ft,(rr,mk) in F[t].items() if s<ft<=perp_exit))<1e-7
 assert abs(net-(grossperp+grossetf+fund-fees-cost))<1e-5
 vals=np.array([CAP]+[x['equity'] for x in path]);pk=np.maximum.accumulate(vals);month=defaultdict(float);prev=CAP
 for x in path:month[dt.datetime.fromtimestamp(x['t'],ET).strftime('%Y-%m')]+=x['equity']-prev;prev=x['equity']
 return dict(start=s,end=end,days=(end-s)/86400,short_leverage=L,hedge_multiplier=hedge_mult,etf_slip_bp=slip,etf_shares=sh,perp_contracts=n,entry_perp=entry,entry_etf=p,initial_perp_margin=margin,idle_cash=idle,notional_mismatch_dollars=n*entry-sh*p,net=net,return_pct=net/CAP*100,simple_annualized_pct=net/CAP*365/((end-s)/86400)*100,funding=fund,gross_perp=grossperp,gross_etf=grossetf,combined_price_component=grossperp+grossetf,fees=fees,slippage=cost,drawdown_pct=float(max(1-vals/pk))*100,minimum_perp_equity=mincoll,maximum_margin_ratio=maxratio,buffer_exit=breach,perp_exit=perp_exit,unhedged_hours=(end-perp_exit)/3600,interest_sensitivity_dollars=intsum,monthly=dict(month),path=path)
for startname,start in [('full',START),('later',SPLIT)]:
 for L in [1,1.5,2,3]:
  for slip in [2,5]:
   z=simulate(start,L,slip);path=z.pop('path');key=f'{startname}/L{L}/slip{slip}';z['key']=key;rows.append(z);paths[key]=path
   print(key,{k:round(z[k],4) if isinstance(z[k],float) else z[k] for k in ['net','simple_annualized_pct','funding','combined_price_component','drawdown_pct','buffer_exit','unhedged_hours']},flush=True)
 # Hedge ratio error stress on conservative1.5x short leverage
 for mult in [.99,1.01]:
  z=simulate(start,1.5,5,mult);path=z.pop('path');key=f'{startname}/L1.5/slip5/hedge{mult}';z['key']=key;rows.append(z);paths[key]=path
save('etf_results.json',rows);save('etf_paths.json.gz',paths)
# Fixed calendar restarts; not a search for the best entry date.
restarts=[]
for month in ['2026-06-04','2026-07-01','2026-08-01','2026-09-01']:
 for lev in [1,1.5,2,3]:
  z=simulate(T(month+'T00:00:00Z'),lev,5);path=z.pop('path');z['key']=f'{month}/L{lev}';restarts.append(z)
save('etf_restarts.json',restarts)
