from pathlib import Path
import pandas as pd,numpy as np
from numba import njit
from round_a import sim as stock_sim,stats
P=Path(__file__).resolve().parent;ROOT=P.parent;V=ROOT/'cover_validation_2026_09';T=ROOT/'trend_bot/research/ridge_width_2026_09'
PAIRS=[('MEDP','PGR'),('ALLE','KR'),('ARES','COST'),('AMGN','CBOE'),('CBOE','KR')]
@njit(cache=True)
def allocate(pr,unitfee,flags,mode,band=.05,weight=.5):
 n=len(pr);q=np.zeros(2);cash=1.;navs=np.zeros(n);turns=np.zeros(n);allocs=np.zeros(n);experts=np.ones(11);ew=np.linspace(0.,1.,11);pending=np.array([weight,1-weight]);totalfee=0.
 for i in range(n):
  val=q*pr[i];nav=cash+val.sum()
  if i>0:
   r=pr[i]/pr[i-1]-1
   for k in range(11):
    ret=ew[k]*r[0]+(1-ew[k])*r[1];drift=ew[k]*(1+r[0])/(1+ret);experts[k]*=(1+ret)*(1-abs(drift-ew[k])*(unitfee[i,0]+unitfee[i,1]))
  act=i==0 or flags[i]
  if mode==1:act=i==0
  if mode==2:act=i==0 or abs(allocs[i-1]-weight)>band
  if mode==5:act=True
  if act:
   # Unit quantities were computed using preceding marks. Execute next mark.
   want=pending*pr[i] if i>0 else np.array([weight,1-weight])
   if mode==3 and i>0:want=(want+val)*.5
   diff=want-val
   for j in range(2):
    if diff[j]<0:
     amt=-diff[j];f=amt*unitfee[i,j];cash+=amt-f;totalfee+=f;q[j]-=amt/pr[i,j];turns[i]+=amt
   required=np.sum(np.maximum(diff,0)*(1+unitfee[i]));scale=min(1.,max(0.,cash)/required) if required>0 else 0.
   for j in range(2):
    if diff[j]>0:
     amt=diff[j]*scale;f=amt*unitfee[i,j];cash-=amt+f;totalfee+=f;q[j]+=amt/pr[i,j];turns[i]+=amt
  navs[i]=cash+np.sum(q*pr[i]);allocs[i]=q[0]*pr[i,0]/navs[i]
  target=weight
  if mode==5:target=np.sum(ew*experts)/experts.sum()
  pending=np.array([target,1-target])*navs[i]/pr[i]
  assert cash>-1e-8
 terminalfee=np.sum(q*pr[-1]*unitfee[-1]);navs[-1]-=terminalfee;totalfee+=terminalfee
 return navs,turns,allocs,totalfee

def core_at_dates(parent,case,dates):
 d=pd.read_parquet(T/f'path_native_{case}_{parent}.parquet');d.index=d.index+pd.Timedelta(minutes=15)
 # Marks as of actual NYSE close, including early closes, each available session.
 import exchange_calendars as xc
 cal=xc.get_calendar('XNYS');schedule=cal.schedule.loc[str(dates[0].date()):str(dates[-1].date())]
 close=pd.DatetimeIndex(schedule['close']);target=pd.Series(close,index=schedule.index.tz_localize(None) if schedule.index.tz is not None else schedule.index).reindex(dates)
 assert target.notna().all()
 out=d.reindex(pd.DatetimeIndex(target),method='ffill');out.index=dates
 return out

def main():
 prices=pd.read_parquet(V/'long_eligible_prices.parquet');rows=[];paths={}
 dates=prices.loc['2018-05-01':'2026-07-02'].index
 for parent in ['baseline','ridge10','width20']:
  for case,fee in [('base',5),('stress',10),('severe',25)]:
   core=core_at_dates(parent,case,dates)
   for capital in [25000.,50000.,100000.]:
    for a,b in PAIRS:
     for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
      ix=dates[(dates>=start)&(dates<=end)];stock=prices.loc[ix,[a,b]];assert stock.notna().all().all()
      sn,_,se=stock_sim(stock.to_numpy(),np.ones(stock.shape,dtype=bool),np.ones(len(ix),dtype=bool),0,fee/1e4)
      sn[-1]/=(1-fee/1e4*se[-1]);cn=capital+core.loc[ix,'net'].to_numpy()-core.loc[ix[0],'net'];assert cn.min()>0
      pr=np.column_stack([cn/capital,sn/sn[0]])
      percontract={'base':1.75,'stress':4.75,'severe':9.5}[case]
      costs=np.column_stack([core.loc[ix,'contracts'].to_numpy()*percontract/cn,np.full(len(ix),fee/1e4)])
      flags={'daily':np.ones(len(ix),dtype=bool),'weekly':np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]],'monthly':np.r_[True,ix.to_period('M')[1:]!=ix.to_period('M')[:-1]]}
      specs=[('core',1,0,1.),('stock_pair',1,0,0.),('static_blend',1,0,.5),('daily',0,0,.5),('weekly',0,0,.5),('monthly',0,0,.5),('band5',2,.05,.5),('band10',2,.1,.5),('half_daily',3,0,.5),('universal',5,0,.5)]
      for name,mode,band,w in specs:
       nav,turn,al,cost=allocate(pr,costs,flags.get(name,flags['daily']),mode,band,w);rows.append(dict(parent=parent,case=case,capital=capital,a=a,b=b,period=period,design=name,overlay_fees=cost,turnover=turn.sum(),mean_core_weight=al.mean(),**stats(nav,ix)))
       if capital==50000 and case=='stress' and period=='full':paths[f'{parent}_{a}_{b}__{name}']=pd.Series(nav,index=ix)
   print(parent,case,'done',flush=True)
 d=pd.DataFrame(rows);d.to_csv(P/'round_b_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_b_paths.parquet');keys=['parent','case','capital','a','b','period'];control=d[d.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(control,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d['sharpe_change']=d.sharpe-d.static_sharpe;d.to_csv(P/'round_b_comparisons.csv',index=False)
 print(d[(d.capital==50000)&(d.case=='stress')].groupby(['period','design'])[['cagr_change','dd_change','sharpe_change']].median().round(4).to_string())
if __name__=='__main__':main()
