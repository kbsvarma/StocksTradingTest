from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
P=Path(__file__).resolve().parent;R=P.parent
model=pd.read_parquet(R/'conditional_stocks_2026_09/mega/trades.parquet');model=model[(model.penalty==100)&(model.cents==.03)&(model.cushion==0)];active=set(model.loc[model.active,'day']);pred=pd.read_parquet(R/'conditional_stocks_2026_09/mega/predictions.parquet');pred=pred[pred.penalty==100]
compat=pd.read_parquet(R/'databento_fresh_2026_09/data/stock_morning_panel.parquet');compat=compat[compat.mega];exact=pd.read_parquet(P/'exact_panel.parquet');rows=[];legs=[];audit=[];random_rows=[];random_days=[];random_modes=[];rng=np.random.default_rng(62206)
costs=['one_cent','three_cents','five_cents','five_bps','borrow3','borrow10','fill_extra']
def leg_arrays(g,entrycol='entry',exitcol='exit'):
 e=g[entrycol].to_numpy();x=g[exitcol].to_numpy();valid=np.isfinite(e)&(e>0);e=np.where(valid,e,1);shares=np.floor(50000/e);missing=~np.isfinite(x)|(x<=0);xx=np.where(missing,e,x);d=shares*(xx-e);loss=-shares*e*.1;L=np.where(missing,loss,d);S=np.where(missing,loss,-d);turn=shares*(e+xx);base=shares*.06+turn*.00005
 fees={'one_cent':shares*.02+turn*.00005,'three_cents':base,'five_cents':shares*.10+turn*.00005,'five_bps':turn*.0005,'borrow3':base,'borrow10':base,'fill_extra':base+turn*.0001}
 return e,xx,shares,valid,missing,L,S,turn,fees
for mode,panel in [('exact',exact),('recovered',exact.assign(exit=exact.exit_recovered)),('compat_all',compat),('compat_model_universe',pred)]:
 for day,g in panel.groupby('day'):
  g=g[np.isfinite(g.r)].copy()
  if mode=='exact':g=g[(g.open>0)&(g.signal>0)]
  g=g.sort_values('symbol').reset_index(drop=True)
  if len(g)<20:continue
  i=int(g.r.idxmax());j=int(g.r.idxmin())
  if i==j:j=(i+1)%len(g)
  for clock in (['standard','delay'] if mode=='exact' else ['standard']):
   e,x,sh,valid,missing,L,S,turn,fees=leg_arrays(g,'entry_delay' if clock=='delay' else 'entry','exit_delay' if clock=='delay' else 'exit');filled=bool(valid[i] and valid[j]);gross=L[i]+S[j] if filled else 0;notional=sh[i]*e[i]+sh[j]*e[j] if filled else 0
   audit.append(dict(mode=mode,clock=clock,day=day,names=len(g),long=g.symbol.iloc[i],short=g.symbol.iloc[j],filled=filled,missing_exit=int(missing[i])+int(missing[j]) if filled else 0,model_selected=day in active))
   for cost in costs:
    fee=fees[cost][i]+fees[cost][j] if filled else 0
    if filled and cost in ['borrow3','borrow10']:fee+=sh[j]*e[j]*(.03 if cost=='borrow3' else .10)/365
    rows.append(dict(mode=mode,clock=clock,day=day,cost=cost,filled=filled,model_selected=day in active,gross=gross,fees=fee,net=gross-fee,notional=notional,share_sides=2*(sh[i]+sh[j]) if filled else 0,turnover=turn[i]+turn[j] if filled else 0))
   if filled:
    for k,side,gr in [(i,1,L[i]),(j,-1,S[j])]:legs.append(dict(mode=mode,clock=clock,day=day,symbol=g.symbol.iloc[k],side=side,entry=e[k],exit=x[k] if not missing[k] else np.nan,shares=sh[k],gross=gr,net=gr-fees['three_cents'][k],missing_exit=bool(missing[k])))
   if mode in ['exact','recovered'] and clock=='standard':
    a=rng.integers(len(g),size=2000);b=(a+rng.integers(1,len(g),size=2000))%len(g);net=np.where(valid[a]&valid[b],L[a]+S[b]-fees['three_cents'][a]-fees['three_cents'][b],0);random_rows.append(net);random_days.append(day);random_modes.append(mode)
 print(mode,'done',flush=True)
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');pd.DataFrame(legs).to_parquet(P/'legs.parquet');pd.DataFrame(audit).to_csv(P/'coverage.csv',index=False);np.savez_compressed(P/'random_controls.npz',net=np.array(random_rows),days=np.array(random_days),modes=np.array(random_modes));out=[];yearly=[]
periods=[('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2018','2027')]
for key,g in x.groupby(['mode','clock','cost']):
 for sample in ['all_dates','model_dates','other_dates']:
  h=g if sample=='all_dates' else g[g.model_selected==(sample=='model_dates')]
  for period,lo,hi in periods:
   t=h[(h.day>=lo)&(h.day<hi)].sort_values('day');z=t.net;c=z.cumsum();f=t[t.filled];wins=z[z>0];loss=z[z<0]
   if t.empty:continue
   out.append(dict(zip(['mode','clock','cost'],key))|dict(sample=sample,period=period,days=len(t),filled=len(f),gross=t.gross.sum(),net=z.sum(),mean=f.net.mean(),win=(f.net>0).mean(),worst=z.min(),best=z.max(),dd=(c-c.cummax().clip(lower=0)).min(),pf=wins.sum()/-loss.sum(),without_best5=z.sum()-z.nlargest(5).sum(),breakeven_cents=(t.gross.sum()-.00005*t.turnover.sum())/t.share_sides.sum()*100))
  for year,t in h.groupby(h.day.str[:4]):yearly.append(dict(zip(['mode','clock','cost'],key))|dict(sample=sample,year=year,days=len(t),filled=int(t.filled.sum()),net=t.net.sum(),mean=t[t.filled].net.mean()))
pd.DataFrame(out).to_csv(P/'scores.csv',index=False);pd.DataFrame(yearly).to_csv(P/'yearly.csv',index=False)
# Match original diagnostic exactly; report, do not force agreement.
old=pd.read_parquet(R/'conditional_stocks_2026_09/matched_controls.parquet');old=old[old.kind=='momentum'];new=x[(x['mode']=='compat_model_universe')&(x.cost=='three_cents')&x.model_selected];join=old[['day','net']].merge(new[['day','net']],on='day',suffixes=('_old','_new'),validate='one_to_one');(P/'reconciliation.json').write_text(json.dumps(dict(n=len(join),old_n=len(old),max_abs_difference=float((join.net_old-join.net_new).abs().max()),recent_old=float(join.loc[join.day>='2025','net_old'].sum()),recent_new=float(join.loc[join.day>='2025','net_new'].sum())),indent=2));print('done',len(x),flush=True)
