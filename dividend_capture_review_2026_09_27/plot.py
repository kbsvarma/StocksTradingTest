from pathlib import Path
import pandas as pd,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
P=Path(__file__).resolve().parent
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,(ax,bx)=plt.subplots(2,1,figsize=(12,8),gridspec_kw={'height_ratios':[2.1,1]},layout='constrained',sharex=True)
for name,label,color in [('buyhold','Buy and hold: same 12 stocks','#174c72'),('rotation_b5_a10','Capture: 5 days before / 10 after','#338b7d'),('rotation_b5_a5','Capture: 5 days before / 5 after','#d7802d'),('rotation_b1_a0','Capture: previous close / ex-date close','#b44d63')]:
 d=pd.read_csv(P/f'daily_{name}.csv',index_col=0,parse_dates=True)
 ax.plot(d.index,d.equity,label=f'{label}  |  ${d.equity.iloc[-1]:,.0f}',color=color,lw=2)
 bx.plot(d.index,d.drawdown*100,color=color,lw=1.2)
ax.set_title('Dividend capture: growth of a $100,000 account',loc='left',fontweight='bold',fontsize=17,pad=20)
ax.set_ylabel('Value of initial $100,000');ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p:f'${v/1000:,.0f}k'))
ax.legend(loc='upper left',frameon=False,fontsize=10);ax.grid(axis='y',alpha=.15)
bx.set_ylabel('Drawdown (%)');bx.grid(axis='y',alpha=.15)
fig.supxlabel('2015–September 25, 2026 | Capture: trailing yield ≥3%, max 4 positions, 0.05% cost per side\nIdle cash earns 0%; before taxes. Capture has less stock exposure than buy and hold.',fontsize=10)
fig.savefig(P/'performance.png',dpi=170,facecolor='white')
