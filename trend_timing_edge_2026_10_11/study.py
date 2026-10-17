from pathlib import Path
import sys,json,gzip
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent;BASE=P.parent/'trend_capture_research_2026_10_11'
sys.path.insert(0,str(BASE))
import portfolio as old
import importlib.util
spec=importlib.util.spec_from_file_location('timing_edge_engine',P/'engine.py');eng=importlib.util.module_from_spec(spec);spec.loader.exec_module(eng)
old.simulate=eng.simulate
def rules(C):
 lp=np.log(C);r=lp.diff();lt=(lp>lp.shift(30)).astype(float);valid=lp.shift(30).notna();out={'always_long':valid.astype(float),'LT30':lt}
 def state(enter,leave):
  a=np.zeros(C.shape);active=np.zeros(C.shape[1])
  for i in range(len(C)):
   for j in range(C.shape[1]):
    if leave.iloc[i,j]:active[j]=0
    elif enter.iloc[i,j]:active[j]=1
   a[i]=active
  return pd.DataFrame(a,index=C.index,columns=C.columns)
 for entry in [2,5]:
  for leave in [10,20]:out[f'break{entry}_exit{leave}']=state(lp>lp.rolling(entry).max().shift(1),lp<lp.rolling(leave).min().shift(1))
 out['break5_exitLT30']=state(lp>lp.rolling(5).max().shift(1),lp<lp.shift(30))
 dd=C/C.rolling(30).max()-1
 out['reclaim_LT30']=np.maximum(lt,state((dd.shift(1)<=-.1)&(lp>lp.rolling(3).max().shift(1)),lp<lp.rolling(10).min().shift(1)))
 comp=r.rolling(5).std().shift(1)<.75*r.rolling(20).std().shift(1)
 out['compression_LT30']=np.maximum(lt,state(comp&(lp>lp.rolling(5).max().shift(1)),lp<lp.rolling(20).min().shift(1)))
 ret3=lp-lp.shift(3);acc=(ret3>0)&(ret3>ret3.shift(3))&(lp>lp.ewm(span=10,adjust=False,min_periods=10).mean())
 out['acceleration_LT30']=np.maximum(lt,state(acc,lp<lp.ewm(span=20,adjust=False,min_periods=20).mean()))
 for h in [7,30]:
  rank=(lp-lp.shift(h)).rank(axis=1,pct=True,method='average');tilt=.5+rank;out[f'tilt{h}']=tilt.div(tilt.sum(axis=1),axis=0)*C.shape[1]
  ranks=(lp-lp.shift(h)).rank(axis=1,ascending=False,method='first');w=(ranks<=2).astype(float);out[f'top2_{h}']=w.div(w.sum(axis=1),axis=0)*C.shape[1]
 return {name:w.where(valid,0).shift(1).fillna(0).to_numpy() for name,w in out.items()}
def periods(C,source):
 end=len(C) if source=='proxy' else C.index.searchsorted(pd.Timestamp('2026-10-10',tz='UTC'))
 if source=='proxy':return [('all',0,end)]+[(str(y),C.index.searchsorted(pd.Timestamp(f'{y}-01-01',tz='UTC')),C.index.searchsorted(pd.Timestamp(f'{y+1}-01-01',tz='UTC'))) for y in range(2022,2026)]
 split=C.index.searchsorted(pd.Timestamp('2026-09-01',tz='UTC'));return [('all',0,end),('early',0,split),('later',split,end)]
def main():
 rows=[]
 for source in ['proxy','venue']:
  for stress in [False,True]:
   C,O,F=old.build(source,stress)
   for name,S in rules(C).items():
    for fundscale in [0.,1.]:
     for period,st,en in periods(C,source):
      r,path,pnl=old.simulate(C,O,F,S,source,stress,st,en,list(range(4)),fundscale)
      r.update(source=source,scenario='combined' if stress else 'base',design=name,funding_enabled=bool(fundscale),period=period,asset_pnl=pnl.tolist());rows.append(r)
      np.savez_compressed(P/'ledgers'/f'{source}_{name}_{fundscale}_{period}_{stress}.npz',equity=path,asset_pnl=pnl)
   print(source,stress,flush=True)
 pd.DataFrame(rows).to_csv(P/'results.csv',index=False)
 d=pd.DataFrame(rows);b=d[d.design=='always_long'][['source','scenario','funding_enabled','period','net']].rename(columns={'net':'benchmark_net'});d=d.merge(b,on=['source','scenario','funding_enabled','period']);d['excess']=d.net-d.benchmark_net
 train=d[(d.source=='proxy')&d.period.isin(['2022','2023','2024'])&~d.design.isin(['always_long','LT30'])]
 score=train.groupby(['design','scenario','funding_enabled']).excess.mean().groupby('design').min().sort_values(ascending=False);winner=score.index[0];(P/'selection.json').write_text(json.dumps({'selected':winner,'gate_passed':bool(score.iloc[0]>0),'criterion':'minimum of four cost/funding scenarios mean annual2022–2024 net excess','scores':score.to_dict()},indent=2));d.to_csv(P/'comparisons.csv',index=False);print(score.to_string())
if __name__=='__main__':
 (P/'ledgers').mkdir(exist_ok=True);main()
