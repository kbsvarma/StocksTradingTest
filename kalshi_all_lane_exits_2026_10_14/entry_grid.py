"""Full entry rebuild and cross-product exit screen; no live client imports."""
from core import *
from functools import lru_cache
sys.path.insert(0,str(P/'production_snapshot'))
from lanes import lane_for
from price_rules import limit_price
Z2='cushion_z2';Z3='premium_z3';LAUNCH=datetime.datetime.fromisoformat('2026-09-12T00:00:00-04:00').timestamp()
WINDOWS=[(1,3),(3,5),(5,10),(10,13),(1,10),(3,10),(1,13)]
LEVELS=[1,1.25,1.5,1.75,1.8,2,2.25,2.5,2.75,3,3.5,4]
def entry_configs():
 out=[dict(id='current',scope='current',z=None,lo=None,hi=None)]
 for scope in ['paired','original','premium']:
  for z in LEVELS:
   for lo,hi in WINDOWS:out.append(dict(id=f'{scope}_z{z:g}_t{lo}_{hi}',scope=scope,z=z,lo=lo,hi=hi))
 for shift in [-.5,-.25,-.1,.25,.5,1]:out.append(dict(id=f'current_shift{shift:g}',scope='shift',z=shift,lo=None,hi=None))
 return out
def rule_params(asset,lane,f,c):
 rule=lane_for(dict(asset=asset,lane=lane));change=c['scope']=='paired' or c['scope']=='original' and lane==Z2 or c['scope']=='premium' and lane==Z3
 if change:return c['lo'],c['hi'],c['z']+(1 if lane==Z3 and c['scope']=='paired' else 0)
 return rule.min_minutes,rule.max_minutes,max(.5,rule.threshold(f['rem'])+(c['z'] if c['scope']=='shift' else 0))
@lru_cache(None)
def entry_fee(price):
 q=D(5);p=D(str(price));ff=(D('.07')*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING);return float((q*p+ff).quantize(D('.0001'),rounding=ROUND_CEILING)-q*p)
def candidates(markets):
 out={}
 for m in markets:
  rows=[]
  for f in m['features']:
   for lane in (Z2,Z3):
    rule=lane_for(dict(asset=m['asset'],lane=lane))
    if not float(rule.floor)-1e-9<=f['ask']<float(rule.ceiling)-1e-9 or f['ask']-f['bid']>.02000001:continue
    signal=dict(asset=m['asset'],lane=lane,signal_ask=f['ask'],side='yes' if f['side']==1 else 'no',price_ranges=m['ranges'])
    cap=float(limit_price(signal)[1]);px=1-quote(m,f['t']+60,-f['side'],'o')
    if not math.isfinite(px) or px<float(rule.floor)-1e-9 or px>cap+1e-9 or f['ask']>cap+1e-9:continue
    px=max(px,f['ask']);fee=entry_fee(px);won=int(f['side']==m['result']);q=5
    rows.append(dict(ticker=m['ticker'],asset=m['asset'],lane=lane,t=f['t'],entered=f['t']+3,close=m['close'],settled=m['settled'],side=f['side'],quantity=5,cost=q*px,entry_fee=fee,entry_friction=.05,hold_net=q*won-q*px-fee-.05,won=won,entry_z=f['z'],entry_sigma=f['sigma'],reference=f['ref'],entry_spot=f['spot'],rem=f['rem'],cap=cap,entry_price=px,reserve=max(q*float(rule.reserve),q*px+fee+.05),feature=f))
  if rows:out[m['ticker']]=sorted(rows,key=lambda r:(r['t'],r['lane']!=Z2))
 return out
def pick(pool,c):
 out=[]
 for rows in pool.values():
  for r in rows:
   lo,hi,gate=rule_params(r['asset'],r['lane'],r['feature'],c)
   if lo<=r['rem']<=hi and r['entry_z']>=gate:
    out.append(dict(r,entry_gate=gate));break
 return sorted(out,key=lambda r:(r['entered'],r['ticker']))
def vector_exits(r,p,cc):
 n=len(cc);T=p['t'];N=len(T);net=np.full(n,r['hold_net']);cut=np.zeros(n,dtype=bool);realized=np.full(n,r['settled']);prices=np.full(n,np.nan);fee=np.zeros(n);triggers=np.full(n,-1)
 if not N:return net,cut,realized,prices,fee,triggers
 thresholds=np.array([threshold(r,c) for c in cc]);graces=np.array([c['grace'] for c in cc]);persist=np.array([c['persist'] for c in cc]);isprice=np.array([c['family']=='price' for c in cc]);obs=np.where(isprice[:,None],p['bc'][None,:],p['z'][None,:])
 breach=np.isfinite(obs)&(obs<thresholds[:,None])&(T[None,:]>=r['entered']+graces[:,None]);idx=np.arange(N);reset=np.maximum.accumulate(np.where(~breach,idx[None,:],-1),axis=1);valid=breach&((idx[None,:]-reset-1)*60>=persist[:,None]);exists=valid.any(axis=1);first=np.argmax(valid,axis=1)
 proceeds=np.array([fee_sale(5,float(px))[0]-.05 if np.isfinite(px) and px>0 else np.nan for px in p['opening']]);fees=np.array([fee_sale(5,float(px))[1] if np.isfinite(px) and px>0 else 0 for px in p['opening']]);exists=exists&np.isfinite(proceeds[first]);w=np.flatnonzero(exists);j=first[w]
 net[w]=proceeds[j]-r['cost']-r['entry_fee']-.05;cut[w]=True;realized[w]=p['exit_t'][j];prices[w]=p['opening'][j];fee[w]=fees[j];triggers[w]=T[j]
 return net,cut,realized,prices,fee,triggers
def main():
 data=pickle.load(gzip.open(P/'inputs.pkl.gz'));cc=rules();configs=entry_configs();save('entry_configs.json',configs);save('exit_configs.json',cc);(P/'entry_screens').mkdir(exist_ok=True);selections={};cache_counts={}
 for a in ASSETS:
  ms=[m for m in data['markets'].values() if m['asset']==a];pool=candidates(ms);cache={};pathcache={};allresults=[];minclose=min(m['close'] for m in ms);maxclose=max(m['close'] for m in ms);selected={};config_hold={}
  print('starting',a,len(ms),'price-qualified candidates',sum(map(len,pool.values())),flush=True)
  for ci,c in enumerate(configs):
   rows=pick(pool,c);nr=len(rows);nets=np.empty((nr,len(cc)));cuts=np.empty((nr,len(cc)),dtype=bool);realtimes=np.empty((nr,len(cc)))
   for i,r in enumerate(rows):
    key=(r['ticker'],r['t'],r['lane']);gkey=key+(round(r['entry_gate'],9),)
    if key not in pathcache:pathcache[key]=path(r,data)
    if gkey not in cache:cache[gkey]=vector_exits(r,pathcache[key],cc)
    nn,cut,rt,*_=cache[gkey];nets[i]=nn;cuts[i]=cut;realtimes[i]=rt
   won=np.array([r['won'] for r in rows],dtype=bool);hold=np.array([r['hold_net'] for r in rows]);close=np.array([r['close'] for r in rows]);fit=(close>=LAUNCH)&(close<SPLIT);later=close>=SPLIT;older=close<LAUNCH
   def aggregate(mask):
    h=hold[mask];w=won[mask];n=nets[mask];c=cuts[mask];delta=n-h[:,None]
    return dict(n=int(mask.sum()),wins=int(w.sum()),losses=int((~w).sum()),hold=float(h.sum()),net=n.sum(axis=0).tolist(),losses_reduced=((delta>1e-8)&(~w[:,None])).sum(axis=0).tolist(),recovered=(delta*(~w[:,None])).sum(axis=0).tolist(),winners_cut=(c&w[:,None]).sum(axis=0).tolist(),winner_cost=(-(delta*w[:,None]).sum(axis=0)).tolist())
   rec=dict(entry_config=c,full=aggregate(np.ones(nr,dtype=bool)),fit=aggregate(fit),later=aggregate(later),older=aggregate(older));allresults.append(rec);config_hold[c['id']]=rec['full']['hold']
   # Selection uses preOct8 gain over holding the SAME proposed entries. Keep
   # hold as an explicit competing exit policy, avoiding forced detrimental stops.
   fitnets=np.array(rec['fit']['net']);best=int(np.argmax(fitnets)) if len(fitnets) else 0;use=best if fitnets[best]>rec['fit']['hold'] else -1
   selected[c['id']]=dict(exit_id=cc[use]['id'] if use>=0 else 'hold',exit_index=use,fit_trades=rec['fit']['n'],fit_hold=rec['fit']['hold'],fit_net=float(fitnets[best]) if use>=0 else rec['fit']['hold'],later_hold=rec['later']['hold'],later_net=rec['later']['net'][use] if use>=0 else rec['later']['hold'],full_hold=rec['full']['hold'],full_net=rec['full']['net'][use] if use>=0 else rec['full']['hold'],trades=nr,frequency_days=(maxclose-minclose)/86400)
   if ci%50==0:print(a,'config',ci,'/',len(configs),'cache',len(cache),flush=True)
  with gzip.open(P/'entry_screens'/f'{a}.json.gz','wt') as f:json.dump(allresults,f,separators=(',',':'))
  selections[a]=selected;cache_counts[a]=dict(cached_entry_gate_pairs=len(cache),unique_entries=len(pathcache),first=minclose,last=maxclose,markets=len(ms));save('entry_selections.json',selections);save('entry_screen_coverage.json',cache_counts)
  # Rank joint configurations using fit net only; require>=30fit entries and
  # preserve later and prelaunch performance as checks, never fit to later wins.
  ranked=sorted([(k,v) for k,v in selected.items() if v['fit_trades']>=30],key=lambda kv:-kv[1]['fit_net']);print('DONE',a,'fit_selected',ranked[:2],flush=True)
if __name__=='__main__':main()
