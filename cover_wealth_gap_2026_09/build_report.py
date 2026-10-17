from pathlib import Path
import json
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
x=pd.read_parquet(P/'common22_all_pairs.parquet');base=x[x.weight_a.eq(.5)&x.cost_bps.eq(10)&x.history_proxy].copy();hype={'GME','AMC','MSTR','COIN'}
base['meme_crypto_flag']=base.a.isin(hype)|base.b.isin(hype)
shape=base[base[['stock_a_multiple','stock_b_multiple']].max(axis=1).le(15)&base.rebalance_multiple.ge(50)&base.over_best.ge(4)].sort_values('over_best',ascending=False)
for name,z in [('top10_all_shape',shape),('top10_non_ai_shape',shape[shape.no_ai]),('top10_non_ai_non_meme_shape',shape[shape.no_ai&~shape.meme_crypto_flag])]:z.head(10).to_csv(P/(name+'.csv'),index=False)
primary=pd.read_csv(P/'common22_larger_gap.csv').head(5);primary.to_csv(P/'primary_shortlist.csv',index=False)
rows=[]
for cap in [.3,.4,.5,.6,.7]:
 z=base[base.no_ai&~base.meme_crypto_flag&base.maxdd.ge(-cap)].sort_values('over_best',ascending=False)
 r=z.iloc[0].to_dict();r['drawdown_cap']=cap;r['eligible_pairs']=len(z);rows.append(r)
risk=pd.DataFrame(rows);risk.to_csv(P/'risk_frontier.csv',index=False)
curves=pd.read_parquet(P/'primary_wealth_paths.parquet')
fig,axes=plt.subplots(2,3,figsize=(15,8),layout='constrained')
for ax,((a,b),g) in zip(axes.flat,curves.groupby(['a','b'],sort=False)):
 ax.plot(g.date,g.strategy,color='#095d8c',lw=1.6,label='Daily 50/50');ax.plot(g.date,g.stock_a,color='#b06621',lw=.8,label=a);ax.plot(g.date,g.stock_b,color='#73657c',lw=.8,label=b);ax.plot(g.date,(g.stock_a+g.stock_b)/2,color='#68716e',lw=.9,ls='--',label='Hold 50/50');ax.set_yscale('log');ax.set_title(f'{a} / {b}: {g.strategy.iloc[-1]:.1f}×');ax.grid(alpha=.2);ax.legend(fontsize=8);ax.set_ylabel('Wealth per $1, logarithmic scale')
axes.flat[-1].axis('off');axes.flat[-1].text(.02,.95,'2004-09-13 to 2026-09-11\n\nDaily fixed 50/50; no leverage.\n10 bp per dollar traded, including entry/exit.\nAdjusted total returns; fractional units.\n\nAll five pairs share INSM.\nHistorical drawdowns: 72%–92%.\n\nPre-2018 corporate actions are not\nindependently reconstructed.\nSelected using the full history;\nthese are not forward return forecasts.',va='top',fontsize=11)
fig.suptitle('Large historical rebalancing effects — with substantial risk',fontsize=15);fig.savefig(P/'wealth_paths.png',dpi=170);plt.close(fig)
# Independently reconstruct full-history alternatives, including matched-cost drawdown.
q=pd.read_parquet(P.parent/'cover_validation_2026_09/long_eligible_prices.parquet');alternatives=[]
for a,b in [('HMY','INSM'),('KGC','NBIX'),('HL','IONS'),('HL','TREX'),('HL','PEGA')]:
 z=q[[a,b]].dropna();g=z.to_numpy()[1:]/z.to_numpy()[:-1];daily=g.mean(axis=1)-.001*np.abs(g[:,0]-g[:,1])/2;daily[-1]=g[-1].mean()*.999;v=np.r_[1/1.001,np.cumprod(daily)/1.001];end=z.iloc[-1]/z.iloc[0]*.999/1.001
 alternatives.append(dict(a=a,b=b,start=str(z.index[0].date()),end=str(z.index[-1].date()),years=(z.index[-1]-z.index[0]).days/365.25,stock_a_multiple=end.iloc[0],stock_b_multiple=end.iloc[1],rebalance_multiple=v[-1],hold_multiple=end.mean(),over_best=v[-1]/end.max(),maxdd=(v/np.maximum.accumulate(np.r_[1,v])[1:]-1).min()))
alt=pd.DataFrame(alternatives);alt.to_csv(P/'long_history_alternatives.csv',index=False)
print('REPORT DATA AND PLOT READY')
