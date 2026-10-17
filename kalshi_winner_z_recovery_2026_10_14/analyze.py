"""Winners-only signed-z path census, with no holding/time filters."""
from pathlib import Path
import json,gzip,pickle,hashlib,math,collections,datetime
import numpy as np
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_all_lane_exits_2026_10_14'
ASSETS=['BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI','USDCAD'];CRYPTO=ASSETS[:4]
LEVELS=[3,2.5,2,1.75,1.5,1.25,1,.75,.5,.25,0,-.25,-.5,-.75,-1,-1.5,-2,-2.5,-3,-4,-5,-6,-8,-10,-15,-20]
DROPS=[.25,.5,.75,1,1.5,2,2.5,3,4,5,6,8,10,15,20]
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def summarize_path(t,z,entry_z):
 finite=np.isfinite(z);ii=np.flatnonzero(finite)
 if not len(ii):return dict(observations=len(z),missing=len(z),minimum=None)
 j=int(ii[np.argmin(z[finite])]);low=float(z[j]);after=z[j+1:];tail=after[np.isfinite(after)]
 return dict(observations=len(z),missing=int((~finite).sum()),minimum=low,min_ts=float(t[j]),entry_to_min_drop=max(0.,entry_z-low),last_z=float(z[ii[-1]]),max_z_after_min=float(tail.max()) if len(tail) else None,rebounded_above_zero_after_min=bool(len(tail) and tail.max()>0),below={str(v):bool(low<v) for v in LEVELS},drops={str(v):bool(entry_z-low>v) for v in DROPS})
def summarize(rows,method):
 known=[r for r in rows if r[method]['minimum'] is not None];mins=[r[method]['minimum'] for r in known];worst=min(known,key=lambda r:r[method]['minimum']) if known else None
 return dict(winners=len(rows),with_observations=len(known),complete=sum(r[method]['missing']==0 for r in known),missing_observations=sum(r[method]['missing'] for r in rows),minimum=min(mins) if mins else None,worst_ticker=worst['ticker'] if worst else None,quantiles=dict(zip(['min','p01','p05','p10','p25','median'],np.quantile(mins,[0,.01,.05,.1,.25,.5]).tolist())) if mins else {},below={str(v):sum(r[method]['minimum']<v for r in known) for v in LEVELS},drops={str(v):sum(r['entry_z']-r[method]['minimum']>v for r in known) for v in DROPS},negative_then_positive=sum(r[method]['minimum']<0 and r[method]['rebounded_above_zero_after_min'] for r in known),negative_and_final_negative=sum(r[method]['minimum']<0 and r[method]['last_z']<0 for r in known))
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz','rb'));wins=[r for r in d['actual'] if r['won']];assert len(wins)==438
 rawcf={a:[] for a in CRYPTO}
 for f in sorted((OLD/'cf_seconds').glob('*.gz')):
  a=f.name.split('_')[0];payload=json.load(gzip.open(f))['response']['data']['payload'];rawcf[a].extend((int(x['time']),float(x['value'])) for x in payload)
 cf={}
 for a,records in rawcf.items():
  records=sorted(set(records));tt=np.array([r[0] for r in records]);vv=np.array([r[1] for r in records]);assert np.all(np.diff(tt)>0);cf[a]=(tt,vv)
 del rawcf
 out=[];paths={}
 for r in wins:
  a=r['asset'];t=np.arange(math.floor(r['entered'])+1,int(r['close']),dtype=np.int64);start,values=ss[a];idx=t-1-start;valid=(idx>=0)&(idx<len(values));spot=np.full(len(t),np.nan);spot[valid]=values[idx[valid]];anchor=None
  if a in CRYPTO:
   anchor=values[int(r['t'])-1-start];assert np.isfinite(anchor) and anchor>0;spot=r['entry_spot']*spot/anchor
  rem=(r['close']-t)/60;z=r['side']*(spot/r['reference']-1)/(r['entry_sigma']*np.sqrt(rem));primary=summarize_path(t,z,r['entry_z'])
  assert primary['missing']==0 and len(t)==int(r['close'])-math.floor(r['entered'])-1
  # Finest available source resolution, with the same1second availability model.
  if a in CRYPTO:
   ct,cv=cf[a];lo=np.searchsorted(ct,(r['entered']-1)*1000,side='right');hi=np.searchsorted(ct,(r['close']-1)*1000,side='left');ft=ct[lo:hi]/1000+1;fv=r['entry_spot']*cv[lo:hi]/anchor;fz=r['side']*(fv/r['reference']-1)/(r['entry_sigma']*np.sqrt((r['close']-ft)/60));fine=summarize_path(ft,fz,r['entry_z']);fine['max_gap_seconds']=float(np.diff(ft).max()) if len(ft)>1 else None
   assert fine['minimum']<=primary['minimum']+1e-8
  else:ft=t;fz=z;fine=dict(primary,max_gap_seconds=1.)
  # Rolling volatility is separately labeled, with unknown stats preserved.
  stats=[d['stats'][a].get(int(k)//60*60) for k in t];sig=np.array([s[1] if s else np.nan for s in stats]);rolling=summarize_path(t,z*r['entry_sigma']/sig,r['entry_z'])
  mt=np.arange((math.floor(r['entered'])//60+1)*60,int(r['close']),60);mstats=[d['stats'][a].get(int(k)) for k in mt];mz=np.array([r['side']*(s[0]/r['reference']-1)/(s[1]*math.sqrt((r['close']-k)/60)) if s else np.nan for k,s in zip(mt,mstats)]);minute=summarize_path(mt,mz,r['entry_z'])
  row={k:r[k] for k in ['ticker','asset','lane','side','entered','close','entry_z','entry_sigma','reference','entry_spot','hold_net']};row.update(primary=primary,finest=fine,rolling=rolling,minute=minute)
  # Trace at exact observed nadir for independent arithmetic/recovery audit.
  j=int(np.argmin(z));row['nadir_evidence']=dict(ts=int(t[j]),source_ts=int(t[j])-1,source_price=float(values[idx[j]]),adjusted_spot=float(spot[j]),reference=r['reference'],sigma=r['entry_sigma'],minutes_remaining=float(rem[j]),anchor=float(anchor) if anchor is not None else None,side=r['side'])
  independent=r['side']*((float(spot[j])-r['reference'])/r['reference'])/(r['entry_sigma']*math.sqrt(rem[j]));assert abs(independent-primary['minimum'])<1e-7
  assert primary['minimum']>=fine['minimum']-1e-8
  out.append(row);paths[r['ticker']]=dict(t=t,z=z,finest_t=ft,finest_z=fz)
 report={m:dict(all=summarize(out,m),assets={a:summarize([r for r in out if r['asset']==a],m) for a in ASSETS},lanes={l:summarize([r for r in out if r['lane']==l],m) for l in ['cushion_z2','premium_z3']}) for m in ['primary','finest','rolling','minute']}
 # A threshold immediately below a sample's minimum is necessarily empty;
 # retain it as a description, never an empirically validated stop.
 for m in report:
  v=report[m]['all']['minimum'];report[m]['all']['next_lower_quarter_z']=math.floor(v*4)/4
 save('winners.json',out);save('summary.json',report)
 with gzip.open(P/'paths.pkl.gz','wb',compresslevel=1) as f:pickle.dump(paths,f)
 save('manifest.json',dict(inputs={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [P/'inputs.pkl.gz',P/'seconds.pkl.gz',OLD/'inputs.pkl.gz',OLD/'seconds.pkl.gz']},winner_count=len(out),methods=['primary:1s checks,1s lag,frozen entry sigma','finest:all5HzCF or1HzPyth,1s lag,frozen sigma','rolling:1s checks,1s lag,completed-minute rolling sigma','minute:completed-minute source and rolling sigma'],no_time_filters=True,no_exit_simulation=True,scope='All438actual winners through Oct14 22:00ET; includes WTI winner settling during study.',audit=dict(independent_nadir_formula_checks=len(out),primary_complete=sum(r['primary']['missing']==0 for r in out),finest_contains_primary=len(out))))
 print(json.dumps(report['primary'],indent=2),flush=True)
 print('FINEST',report['finest']['all'],flush=True)
 print('LOWEST',[(r['ticker'],r['primary']['minimum'],r['finest']['minimum'],r['primary']['last_z']) for r in sorted(out,key=lambda r:r['primary']['minimum'])[:12]],flush=True)
if __name__=='__main__':main()
