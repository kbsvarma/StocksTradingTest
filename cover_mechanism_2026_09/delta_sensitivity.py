from pathlib import Path
import pickle,pandas as pd,numpy as np
from scipy.special import ndtr
from options import delta,hedge
P=Path(__file__).resolve().parent;rows=[]
import exchange_calendars as xc
schedule=xc.get_calendar('XNYS').schedule;closes={str(d.date()):int(t.tz_convert('America/New_York').hour*60+t.tz_convert('America/New_York').minute) for d,t in schedule.close.items()}
for sym in ['spy','iwm']:
 with (P/f'{sym}_selected_quotes.pkl').open('rb') as file:book=pickle.load(file)
 for z in book:
  s=z['spot'];k=z['strike'];day=z['day'];exp=pd.Timestamp(z['expiry']);t=((exp-pd.Timestamp(day)).days+(closes.get(day,960)-z['minutes'])/1440)/365;mid=(z['bid']+z['ask'])/2;total=mid.sum(1);de,iv=delta(s,k,t,total)
  if not np.isfinite(de[0]):continue
  frozen=2*ndtr((np.log(s/k)+.5*iv[0]**2*t)/(iv[0]*np.sqrt(t)))-1
  forward=k+mid[:,0]-mid[:,1];marketde,_=delta(forward,k,t,total)
  option=(z['bid'][-1].sum()-z['ask'][1].sum())*100
  for model,d in [('observed_iv',de),('entry_iv_frozen',frozen),('parity_forward',marketde)]:
   for case,fee,bps in [('base',.65,.0001),('stress',1.30,.0003)]:
    for design,step,band in [('static_delta',0,0),('every15',15,0),('every60',60,0),('band10',0,10)]:
     hp,hc,turn,peak,n=hedge(s[1:],-100*d[:-1],step,band,bps);rows.append(dict(symbol=sym,day=day,tenor=z['tenor'],model=model,case=case,design=design,net=option-4*fee+hp,hedge_pnl=hp,valid_delta_fraction=np.isfinite(d).mean()))
x=pd.DataFrame(rows);flags=pd.concat([pd.read_parquet(P/f'{sym}_hedge_trades.parquet').query("design=='unhedged' and case=='base'")[['day','tenor','missing_exit']].assign(symbol=sym) for sym in ['spy','iwm']]);x=x.merge(flags,on=['symbol','day','tenor']);x.to_parquet(P/'delta_sensitivity_trades.parquet');out=[]
for keys,g in x.groupby(['symbol','tenor','model','case','design']):
 for period,lo,hi in [('all','2021','2027'),('early','2021','2023'),('middle','2023','2025'),('recent','2025','2027')]:
  z=g[g.day.ge(lo)&g.day.lt(hi)]
  for coverage in ['all_conservative','complete_exit']:
   zz=z if coverage=='all_conservative' else z[~z.missing_exit]
   if len(zz):out.append(dict(zip(['symbol','tenor','model','case','design'],keys),period=period,coverage=coverage,n=len(zz),mean=zz.net.mean(),net=zz.net.sum()))
pd.DataFrame(out).to_csv(P/'delta_sensitivity_scores.csv',index=False);print('delta sensitivity',len(rows),'trades',flush=True)
