from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter,PercentFormatter
P=Path(__file__).resolve().parent
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold','figure.facecolor':'#f7f8fa','axes.facecolor':'#f7f8fa'})
names=[('CHPY_hold','Hold / reinvest','#6b7280'),('capture_b3_a0_close_chpy20','Trend-filtered capture','#176b58'),('capture_b1_a0_open','Overnight capture','#2563eb'),('recover_price_s0_h20','Wait for price recovery','#c26d19')]
fig,(ax,dd)=plt.subplots(2,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
for name,label,color in names:
    d=pd.read_csv(P/'daily'/f'{name}_test2026.csv',parse_dates=['Date']).set_index('Date')
    ax.plot(d.index,d.equity,label=f'{label}: ${d.equity.iloc[-1]:,.0f}',color=color,lw=2)
    dd.plot(d.index,d.drawdown,color=color,lw=1.5)
ax.axhline(10000,c='#9ca3af',lw=.8,ls='--');ax.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'${x:,.0f}'));ax.legend(loc='upper left',frameon=False)
ax.set_title('CHPY: what happened to a $10,000 account in 2026',loc='left',pad=16)
ax.set_ylabel('Total account value');dd.set_ylabel('Decline from peak');dd.yaxis.set_major_formatter(PercentFormatter(1));dd.set_xlabel('January 2–September 25, 2026')
for a in [ax,dd]:a.grid(axis='y',alpha=.15)
fig.text(.08,.02,'Whole shares • 0.10% execution cost per side • Actual payout dates • Before tax\nHistorical simulation; rule design and CHPY selection were retrospective. Not a future income forecast.',fontsize=9,color='#4b5563')
fig.tight_layout(rect=[0,.065,1,1]);fig.savefig(P/'equity_and_drawdown.png',dpi=160);plt.close(fig)

r=pd.read_csv(P/'results.csv');s=pd.read_csv(P/'sensitivity.csv')
fig,(ax,ax2)=plt.subplots(1,2,figsize=(12,4.8))
for name,label,color in names[1:3]:
    x=pd.concat([r[(r.name==name)&(r.period=='test2026')],s[(s.name==name)&(s.period=='test2026')&(s.allocation==1)&s.whole_shares]]).sort_values('cost_bps')
    ax.plot(x.cost_bps,x.profit,marker='o',label=label,color=color,lw=2)
ax.axhline(0,color='#6b7280',lw=.8);ax.set_xlabel('Execution cost per side (basis points)');ax.set_ylabel('Net account profit');ax.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'${x:,.0f}'));ax.set_title('The overnight edge is cost-sensitive',loc='left');ax.legend(frameon=False)
for name,label,color in names[1:3]:
    d=pd.read_csv(P/'daily'/f'{name}_test2026.csv',parse_dates=['Date']).set_index('Date')
    w=d.equity.resample('W-FRI').last();p=w-pd.Series([10000]+w.tolist()[:-1],index=w.index)
    ax2.plot(p.index,p,label=label,color=color,lw=1.4)
ax2.axhline(0,color='#6b7280',lw=.8);ax2.set_title('Weekly profit was irregular',loc='left');ax2.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'${x:,.0f}'));ax2.tick_params(axis='x',rotation=30)
for a in [ax,ax2]:a.grid(axis='y',alpha=.15)
fig.text(.08,.02,'2026 test period • $10,000 initial account • Profits reinvested • Before tax',fontsize=9,color='#4b5563')
fig.tight_layout(rect=[0,.045,1,1]);fig.savefig(P/'costs_and_weekly_profit.png',dpi=160);plt.close(fig)
