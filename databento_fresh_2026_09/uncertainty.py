from common import *
import json
paths=sorted(O.glob('daily_portfolio_base_*.parquet'))+[O/'followup_base_ensemble_weekly_lag0_HG_SI.parquet'];monthly={}
for f in paths:
 a=pd.read_parquet(f);a=a[(a.index>='2019')&(a.index<'2025')];a.index=a.index.tz_localize(None) if a.index.tz else a.index;monthly[f.stem]=a.net.resample('MS').sum().reindex(pd.date_range('2019-01-01','2024-12-01',freq='MS'),fill_value=0)
x=pd.DataFrame(monthly).to_numpy();mean=x.mean(0);se=x.std(0,ddof=1)/np.sqrt(len(x));rng=np.random.default_rng(20260906);boot=np.array([x[rng.integers(0,len(x),len(x))].mean(0) for _ in range(5000)]);z=(boot-mean)/se;critical=np.quantile(z.max(axis=1),.95);out=pd.DataFrame({'design':monthly.keys(),'annual_mean':12*mean,'annual_se':12*se,'pointwise_lower95':12*(mean-1.645*se),'family_lower95':12*(mean-critical*se)});out.to_csv(O/'selection_adjusted.csv',index=False);print(out.tail(1).round(3).to_string(index=False));print('Compared',len(paths),'critical',critical)
for name in ['followup','daily','intraday','stock','option','innovation','ice']:
 f=O/f'{name}_scoreboard.csv'
 if not f.exists():continue
 a=pd.read_csv(f)
 if name=='followup':print(a.query("group=='HG_SI'")[['case','design','period','net','dd','sharpe','worst_year']].round(3).to_string(index=False))
