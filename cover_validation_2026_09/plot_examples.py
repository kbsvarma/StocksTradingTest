from pathlib import Path
import pandas as pd,numpy as np,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter,LogLocator,NullFormatter
P=Path(__file__).resolve().parent;pairs=[('MEDP','PGR'),('ALLE','KR'),('SMCI','VKTX'),('COST','HALO')]
fig=plt.figure(figsize=(15,11));grid=fig.add_gridspec(4,2,height_ratios=[3,1,3,1],hspace=.42,wspace=.20)
for k,(a,b) in enumerate(pairs):
 frames=[]
 for s in [a,b]:
  d=pd.read_parquet(P/'long_daily'/(s+'.parquet'));d.index=pd.to_datetime(d.index.date);frames.append(d['Adj Close'].rename(s))
 q=pd.concat(frames,axis=1).dropna();g=q.to_numpy()[1:]/q.to_numpy()[:-1];c=.001;net=g.mean(axis=1)-c*abs(g[:,0]-g[:,1])/2;net[-1]=g[-1].mean()*(1-c);nav=1000*np.r_[1/(1+c),np.cumprod(net)/(1+c)];legs=1000*q.to_numpy()/q.iloc[0].to_numpy()/(1+c);legs[-1]*=1-c;hold=legs.mean(axis=1);row=(k//2)*2;col=k%2;ax=fig.add_subplot(grid[row,col]);ddax=fig.add_subplot(grid[row+1,col],sharex=ax)
 ax.plot(q.index,nav,color='black',lw=1.7,label='Daily 50/50');ax.plot(q.index,hold,color='#777777',lw=1.3,label='Initial 50/50 hold');ax.plot(q.index,legs[:,0],color='#aaaaaa',lw=.8,ls='--',label=a);ax.plot(q.index,legs[:,1],color='#aaaaaa',lw=.8,ls=':',label=b);ax.set_yscale('log');ax.yaxis.set_major_locator(LogLocator(base=10,subs=(1,2,5)));ax.yaxis.set_minor_formatter(NullFormatter());ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'${x:,.0f}'));ax.grid(alpha=.16);ax.set_title(f'{a} / {b}  |  {q.index[0]:%Y-%m} to {q.index[-1]:%Y-%m}',loc='left',fontweight='bold');ax.legend(loc='upper left',fontsize=8,ncol=2,frameon=False)
 dd=100*(nav/np.maximum.accumulate(np.r_[1000.,nav])[1:]-1);ddax.fill_between(q.index,dd,0,color='#b0b0b0');ddax.set_ylim(min(-30,dd.min()*1.1),0);ddax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:.0f}%'));ddax.grid(alpha=.16);ddax.set_ylabel('Drawdown',fontsize=8);plt.setp(ax.get_xticklabels(),visible=False)
fig.suptitle('Compounding and the losses endured along the way',x=.06,ha='left',fontweight='bold',fontsize=17);fig.text(.06,.035,'Historical examples, not a ranking. $1,000 initial capital; daily fixed 50/50; 10 bp costs on traded value; dividends reinvested; pre-tax.\nOlder histories use the free source. Independent venue-price and execution checks begin in May 2018.',fontsize=9,color='#444444');fig.subplots_adjust(left=.07,right=.98,top=.93,bottom=.10);fig.savefig(P/'compounding_and_drawdown.png',dpi=160);plt.close(fig)
print('DONE',flush=True)
