from pathlib import Path
import pandas as pd,numpy as np,json
from numba import njit,prange,set_num_threads
set_num_threads(4)
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';O=P.parent/'cover_exhaustive_2026_09'
q=pd.read_parquet(O/'price_matrix.parquet');parts=[q]
for f in (V/'expanded_daily').glob('*.parquet'):
 if f.stem in q:continue
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);parts.append(d['Adj Close'].rename(f.stem).reindex(q.index))
x=pd.concat(parts,axis=1);x=x.loc[:,~x.columns.duplicated()];starts={}
for s in x:
 a=x[s];good=a.notna()&a.gt(0);bad=np.flatnonzero(~good.to_numpy());start=bad[-1]+1 if len(bad) else 0
 if start<len(x) and (x.index[-1]-x.index[start]).days>=8*365.25:starts[s]=start
x=x[list(starts)];x.to_parquet(P/'broad_local_prices.parquet');names=x.columns.to_numpy();px=x.to_numpy().T.copy();start=np.array(list(starts.values()));aa,bb=np.triu_indices(len(names),1)
@njit(parallel=True,cache=True)
def calc(px,starts,aa,bb):
 out=np.zeros((len(aa),6))
 for k in prange(len(aa)):
  a,b=aa[k],bb[k];s=max(starts[a],starts[b]);wa=px[a,-1]/px[a,s]*.999/1.001;wb=px[b,-1]/px[b,s]*.999/1.001;w=1/1.001;peak=1.;dd=0.
  for t in range(s+1,px.shape[1]):
   ga=px[a,t]/px[a,t-1];gb=px[b,t]/px[b,t-1];w*=.5*(ga+gb)-(.001*.5*abs(ga-gb) if t<px.shape[1]-1 else 0)
   if t==px.shape[1]-1:w*=.999
   peak=max(peak,w);dd=min(dd,w/peak-1)
  out[k]=np.array([s,wa,wb,w,w/max(wa,wb),dd])
 return out
r=calc(px,start,aa,bb);z=pd.DataFrame(r,columns=['start_index','stock_a_multiple','stock_b_multiple','rebalance_multiple','over_best','maxdd']);z.insert(0,'b',names[bb]);z.insert(0,'a',names[aa]);z['start']=z.start_index.map(lambda a:str(x.index[int(a)].date()));z['end']=str(x.index[-1].date());old=set(pd.read_parquet(V/'long_eligible_prices.parquet').columns);z['new_pair']=~(z.a.isin(old)&z.b.isin(old));z=z.sort_values('over_best',ascending=False);z.to_parquet(P/'broad_all_pairs.parquet',index=False);z[z.new_pair].head(100).to_csv(P/'broad_new_top100.csv',index=False)
(P/'broad_counts.json').write_text(json.dumps(dict(stocks=len(names),pairs=len(z),additional_pairs=int(z.new_pair.sum()),stocks_added=sorted(set(names)-old)),indent=2));print('BROAD',len(names),len(z),'new',z.new_pair.sum(),flush=True);print(z[z.new_pair].head(15).to_string(index=False),flush=True)
