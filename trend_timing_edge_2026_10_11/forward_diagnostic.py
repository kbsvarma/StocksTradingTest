from rotation import *
rows=[];summ=[]
for source in ['proxy','venue','transfer']:
 C,O,F=transfer_build(False) if source=='transfer' else old.build(source,False);S=extended(C)['weekly_top2'];mid=(O.iloc[:,::2].to_numpy()+O.iloc[:,1::2].to_numpy())/2
 for i in range(len(C)-7):
  if C.index[i].weekday()!=1 or not np.isfinite(mid[i]).all() or not np.isfinite(mid[i+7]).all() or S[i].sum()!=4:continue
  ret=mid[i+7]/mid[i]-1;selected=np.dot(S[i]/4,ret);equal=ret.mean();rows.append(dict(source=source,date=str(C.index[i].date()),year=C.index[i].year,selected=selected,equal=equal,excess=selected-equal))
d=pd.DataFrame(rows);d.to_csv(P/'forward_weeks.csv',index=False)
rng=np.random.default_rng(111026)
for source,g in d.groupby('source'):
 for label,q in [('all',g),('2022_2024',g[g.year<=2024]),('2025',g[g.year==2025])]:
  if len(q)<2:continue
  a=q.excess.to_numpy();n=len(a);boot=[]
  for b in range(2000):
   starts=rng.integers(0,n,size=(n+3)//4);indices=np.concatenate([(s+np.arange(4))%n for s in starts])[:n];boot.append(a[indices].mean())
  summ.append(dict(source=source,period=label,weeks=n,gross_mean_excess_bps=1e4*a.mean(),ci_lower_bps=1e4*np.quantile(boot,.025),ci_upper_bps=1e4*np.quantile(boot,.975),positive_weeks=int((a>0).sum()),mean_without_best3_bps=1e4*(a.sum()-np.sort(a)[-3:].sum())/max(1,n-3)))
pd.DataFrame(summ).to_csv(P/'forward_summary.csv',index=False);print(pd.DataFrame(summ).round(2).to_string(index=False))
