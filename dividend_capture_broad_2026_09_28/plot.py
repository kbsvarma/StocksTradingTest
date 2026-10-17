from pathlib import Path
import pandas as pd,numpy as np,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
P=Path(__file__).resolve().parent
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,(ax,bx)=plt.subplots(1,2,figsize=(13,5.5),gridspec_kw={'width_ratios':[1.35,1]},layout='constrained')
for tag,label,col in [('QQQI_all_buyhold','Buy and hold','#174c72'),('QQQI_all_b10_a3','Best hindsight window: −10 / +3','#418777'),('QQQI_all_b5_a5','Capture: −5 / +5','#d18828'),('QQQI_all_b1_a1','Capture: −1 / +1','#ac5165')]:
 d=pd.read_csv(P/'daily'/f'{tag}.csv',index_col=0,parse_dates=True)
 ax.plot(d.index,d.equity,label=f'{label}: ${d.equity.iloc[-1]:,.0f}',color=col,lw=2)
ax.set_title('QQQI: $100,000 with cash funding constraints',loc='left',fontweight='bold');ax.legend(frameon=False,fontsize=9,loc='upper left');ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p:f'${v/1000:,.0f}k'));ax.grid(alpha=.15);ax.tick_params(axis='x',rotation=30)
s=pd.read_csv(P/'security_portfolios.csv');q=s[(s.ticker=='QQQI')&(s.period=='all')&(~s.buyhold)]
z=q.pivot(index='before',columns='after',values='CAGR')*100
im=bx.imshow(z,cmap='RdYlGn',vmin=-15,vmax=20,aspect='auto');bx.set_xticks(range(len(z.columns)),z.columns);bx.set_yticks(range(len(z.index)),z.index)
bx.set_xlabel('Sell sessions after ex-date (0 = ex-date close)');bx.set_ylabel('Buy sessions before ex-date');bx.set_title('QQQI annualized return across all 20 windows',loc='left',fontweight='bold')
for i in range(len(z)):
 for j in range(len(z.columns)):bx.text(j,i,f'{z.iloc[i,j]:.1f}%',ha='center',va='center',fontsize=11)
fig.supxlabel('January 30, 2024–September 25, 2026. 0.05% cost per side; before taxes.\nCash distributions included once; actual QQQI payment dates. Hindsight best is not a validated forecast.',fontsize=10)
fig.savefig(P/'qqqi_results.png',dpi=170,facecolor='white')
r=pd.read_csv(P/'rotation_portfolios.csv');r=r[(r.period=='common2025')&(r.before==5)&(r.after==5)]
ot=pd.read_csv(P/'open_tail_rotation.csv')
for ix,row in r[~r.buyhold].iterrows():
 oo=ot[ot.pool==row.pool].iloc[0]
 for col in oo.index.intersection(r.columns):r.loc[ix,col]=oo[col]
labels={'option_income':'Option-income ETFs','higher_risk_option_income':'Higher-risk option funds','us_dividend_etf':'US dividend ETFs','global_dividend_etf':'Global dividend ETFs','reit':'REITs','bdc':'BDCs','preferred_etf':'Preferred-stock ETFs','closed_end_fund':'Closed-end funds','dividend_stock':'Dividend stocks','core_income':'Core income rotation','broad':'All 92 rotation'}
fig,ax=plt.subplots(figsize=(12,7),layout='constrained');yy=np.arange(len(labels))
a=[r[(r.pool==c)&(~r.buyhold)].iloc[0].total_return*100 for c in labels];b=[r[(r.pool==c)&r.buyhold].iloc[0].total_return*100 for c in labels]
ax.barh(yy-.18,a,.35,label='Dividend capture: −5 / +5',color='#b77529');ax.barh(yy+.18,b,.35,label='Buy and hold same group',color='#174c72')
ax.set_yticks(yy,list(labels.values()));ax.invert_yaxis();ax.axvline(0,color='#555',lw=.8);ax.set_xlabel('Cumulative account return (%)');ax.set_title('Broader coverage: rotation results on a matching period',loc='left',fontsize=17,fontweight='bold',pad=18);ax.legend(loc='lower right',frameon=False);ax.grid(axis='x',alpha=.15)
fig.supxlabel('January 2, 2025–September 25, 2026 | Initial $100,000 | Costs 0.05% per side | Before taxes\nCapture: max 4 positions; includes open positions at cutoff; cash earns zero. Stock exposure differs; see report for matched-exposure checks.',fontsize=10)
fig.savefig(P/'category_results.png',dpi=170,facecolor='white')
