from core import *
B=(1,3,5,10);A=(0,1,3,5,10);earliest=DATES.searchsorted('2015-01-01');rows=[]
for s in SYMS:
 if CAT[s]=='benchmark':continue
 d=DATA[s];c=d['close'];div=d['div'];cum=np.r_[0,np.cumsum(div)];start=max(earliest,d['start']);events=np.flatnonzero(div>0)
 controls={}
 for length in sorted(set(b+a for b in B for a in A)):
  ii=np.arange(start,END-length+1);ends=ii+length
  vv=c[ends]*.9995/(c[ii]*1.0005)-1
  no_div=(cum[ends+1]-cum[ii+1])==0
  for yr in range(2015,2027):
   sel=(DATES[ii].year==yr)&no_div
   controls[(length,yr)]=float(np.mean(vv[sel])) if sel.any() else np.nan
 for b in B:
  for a in A:
   for k in events:
    i,j=k-b,k+a
    if i<start or j>END:continue
    payouts=cum[j+1]-cum[i+1];gross=(c[j]+payouts)/c[i]-1;net=(c[j]*.9995+payouts)/(c[i]*1.0005)-1
    ctrl=controls[(b+a,DATES[i].year)]
    rows.append((s,CAT[s],b,a,i,k,j,str(DATES[i].date()),str(DATES[k].date()),str(DATES[j].date()),DATES[k].year,str(DATES[k].to_period('Q')),c[i],c[j],payouts,payouts/c[i],c[j]/c[i]-1,gross,net,ctrl,net-ctrl,d['ttm'][i],i-d['start']>=252,d['decl'].get(k,-1),int((div[i+1:j+1]>0).sum())))
cols='ticker category before after i k j entry ex_date exit year quarter entry_price exit_price distributions distribution_yield price_return gross_return net_return control_mean excess ttm_yield year_history declaration_index payouts_in_window'.split()
e=pd.DataFrame(rows,columns=cols);e.to_csv(P/'events.csv',index=False)
# Summary groups and quarter-block bootstrap, accounting for date clusters across assets.
def ci(g,col):
 z=g.dropna(subset=[col]).groupby('quarter')[col].agg(['sum','count']).to_numpy()
 if len(z)<4:return [np.nan,np.nan]
 rng=np.random.default_rng(8391);ix=rng.integers(len(z),size=(1000,len(z)));v=z[ix,0].sum(axis=1)/z[ix,1].sum(axis=1)
 return np.quantile(v,[.025,.975])
def summarize(g,confidence=False):
 r=g.net_return;pos=r[r>0];neg=r[r<0]
 x=dict(events=len(g),mean=r.mean(),median=r.median(),win_rate=(r>0).mean(),avg_win=pos.mean(),avg_loss=neg.mean(),worst=r.min(),best=r.max(),profit_factor=pos.sum()/(-neg.sum()) if len(neg) else np.nan,distribution=g.distribution_yield.mean(),price=g.price_return.mean(),control=g.control_mean.mean(),excess=g.excess.mean())
 if confidence:x.update(zip(['ci_low','ci_high'],ci(g,'net_return')));x.update(zip(['excess_ci_low','excess_ci_high'],ci(g,'excess')))
 return x
ss=[];cc=[]
for period,start in [('all','2015-01-01'),('common2024','2024-02-01'),('common2025','2025-01-02')]:
 ix=DATES.searchsorted(start);names=[s for s in SYMS if (period=='all' or DATA[s]['start']<=ix)]
 x=e[(e.i>=ix)&e.ticker.isin(names)]
 for (s,b,a),g in x.groupby(['ticker','before','after']):ss.append(dict(period=period,ticker=s,category=CAT[s],before=b,after=a,**summarize(g,confidence=(b,a) in [(1,1),(5,5)])))
 for (cat,b,a),g in x.groupby(['category','before','after']):cc.append(dict(period=period,category=cat,before=b,after=a,**summarize(g,True)))
pd.DataFrame(ss).to_csv(P/'security_event_summary.csv',index=False);pd.DataFrame(cc).to_csv(P/'category_event_summary.csv',index=False)
# Expanding one-year training selection; only known complete exits before new year.
wf=[]
for cat in [c for c in U if c!='benchmark']:
 for year in range(2016,2027):
  train=e[(e.category==cat)&(pd.to_datetime(e.entry).dt.year==year-1)&(pd.to_datetime(e.exit).dt.year==year-1)]
  score=train.groupby(['before','after']).net_return.agg(['mean','count']);score=score[score['count']>=50]
  if score.empty:continue
  best=score.sort_values('mean',ascending=False).index[0];test=e[(e.category==cat)&(pd.to_datetime(e.entry).dt.year==year)&(e.before==best[0])&(e.after==best[1])]
  if len(test):wf.append(dict(category=cat,test_year=year,before=best[0],after=best[1],train_events=int(score.loc[best,'count']),train_mean=float(score.loc[best,'mean']),**summarize(test)))
pd.DataFrame(wf).to_csv(P/'walkforward_events.csv',index=False)
print('Event rows',len(e),'distinct distributions',len(e[['ticker','k']].drop_duplicates()),'securities',e.ticker.nunique(),flush=True)
print(pd.DataFrame(ss).query("ticker=='QQQI' and period=='all'")[['before','after','events','mean','win_rate','excess']].round(5).to_string(index=False),flush=True)
