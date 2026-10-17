from pathlib import Path
import numpy as np,pandas as pd,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
fig,ax=plt.subplots(1,2,figsize=(12,4.8))
for a,funding in zip(ax,[0.,1.]):
 for name,label,color in [('always_long','Equal-weight holding','#7b8490'),('weekly_top_half','Weekly strongest half','#176b55'),('risk_adjusted_half','Risk-normalized strength','#345cb3')]:
  x=np.load(P/'ledgers'/f'eight_{name}_{funding}_False.npz');a.plot(pd.date_range('2022-01-01',periods=len(x['equity'])),x['equity'],label=label,color=color,lw=1.5)
 a.axhline(100,lw=.7,color='#aaaaaa');a.set_title('Funding removed; trading costs retained' if funding==0 else 'Base fees, friction and assumed funding');a.set_ylabel('Equity from one $100 account');a.grid(alpha=.15);a.tick_params(axis='x',rotation=25);a.legend(fontsize=8)
fig.suptitle('Eight-asset relative strength: higher historical returns, substantial drawdowns',fontsize=12)
fig.text(.5,.012,'2022–2025 underlying-price proxy. 50% target gross exposure. Exploratory; not historical venue execution or a forecast.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.04,1,.94));fig.savefig(P/'comparison.png',dpi=170)
