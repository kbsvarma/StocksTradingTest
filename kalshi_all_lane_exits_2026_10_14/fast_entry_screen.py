"""Joint entry/exit sensitivity on explicitly source-covered opportunities.
Never interprets minute bid envelopes as recorded subminute fills.
"""
from entry_grid import *
def quickpath(r,d,ss,interval):
 m=d['markets'][r['ticker']];tt=np.arange((int(r['entered'])//interval+1)*interval,int(r['close'])-1,interval,dtype=int);start,values=ss[r['asset']];jj=tt-1-start;valid=(jj>=0)&(jj<len(values));spots=np.full(len(tt),np.nan);spots[valid]=values[jj[valid]]
 if r['asset'] in CRYPTO:
  j=int(r['t'])-1-start;anchor=values[j] if 0<=j<len(values) else np.nan;spots=r['entry_spot']*spots/anchor
 z=r['side']*(spots/r['reference']-1)/(r['entry_sigma']*np.sqrt((r['close']-tt)/60));bb=tt//60*60;ends=(tt+1)//60*60+60
 def qq(times,suffix):
  cache={t:quote(m,t,r['side'],suffix) for t in set(times)};return np.array([cache[t] for t in times])
 return dict(t=tt,z=z,bc=qq(bb,'c'),low=qq(ends,'l'),high=qq(ends,'h'),exit_t=tt+1)
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz'));choices=json.loads((P/'portfolio_fit_choices.json').read_text());ecs={c['id']:c for c in entry_configs()}
 wanted=[('drop',v,g,p) for v in [.5,1,1.5,2] for g,p in [(0,0),(120,30)]]+[(f,v,0,0) for f,v in [('absolute',0),('absolute',1),('gate',.5),('gate',1),('fraction',.5),('price',.1),('price',.2)]]
 cc=[c for c in rules() if (c['family'],c['value'],c['grace'],c['persist']) in wanted]
 results=[]
 for a in ASSETS:
  pool=candidates([m for m in d['markets'].values() if m['asset']==a]);ids=list(dict.fromkeys(['current',choices[a]]+[f'paired_z{z:g}_t{lo}_{hi}' for z in [1.25,1.5,1.75,1.8,2,2.5,3,4] for lo,hi in [(1,10),(3,10)]]));cache={};coverage={}
  for eid in ids:
   rows=pick(pool,ecs[eid]);complete=[]
   for r in rows:
    key=(r['ticker'],r['t'],r['lane'])
    if key not in coverage:
     p=quickpath(r,d,ss,1);coverage[key]=len(p['t'])>0 and bool(np.isfinite(p['z']).all())
    if coverage[key]:complete.append(r)
   for interval in [1,10,30,60]:
    lo=[];hi=[];cuts=[]
    for r in complete:
     key=(r['ticker'],r['t'],r['lane'],r['entry_gate'],interval)
     if key not in cache:
      p=quickpath(r,d,ss,interval);ll=[exit_result(r,c,p,'low') for c in cc];hh=[exit_result(r,c,p,'high') for c in cc];cache[key]=([x['net'] for x in ll],[x['net'] for x in hh],[x['exit'] for x in ll])
     l,h,c=cache[key];lo.append(l);hi.append(h);cuts.append(c)
    won=np.array([r['won'] for r in complete],dtype=bool);hold=np.array([r['hold_net'] for r in complete]);times=np.array([r['close'] for r in complete]);low=np.array(lo).reshape(-1,len(cc));high=np.array(hi).reshape(-1,len(cc));cut=np.array(cuts,dtype=bool).reshape(-1,len(cc));periods={}
    for period,mask in [('full',np.ones(len(complete),dtype=bool)),('fit',(times>=LAUNCH)&(times<SPLIT)),('later',times>=SPLIT)]:
     w=won[mask];delta=low[mask]-hold[mask,None];periods[period]=dict(trades=int(mask.sum()),wins=int(w.sum()),losses=int((~w).sum()),hold=float(hold[mask].sum()),low=low[mask].sum(axis=0).tolist(),high=high[mask].sum(axis=0).tolist(),losses_reduced=((delta>1e-8)&~w[:,None]).sum(axis=0).tolist(),recovered=(delta*~w[:,None]).sum(axis=0).tolist(),winners_cut=(cut[mask]&w[:,None]).sum(axis=0).tolist(),winner_cost=(-(delta*w[:,None]).sum(axis=0)).tolist())
    results.append(dict(asset=a,entry=eid,interval=interval,all_candidates=len(rows),source_complete=len(complete),source_excluded=len(rows)-len(complete),periods=periods))
   print(a,eid,'source complete',len(complete),'/',len(rows),flush=True)
  save('fast_entry_screen.json',dict(exit_rules=cc,results=results))
if __name__=='__main__':main()
