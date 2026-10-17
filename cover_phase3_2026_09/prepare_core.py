from pathlib import Path
import sys,importlib.util,json
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent;R=P.parent/'trend_bot/research/ridge_width_2026_09'
src=(R.parent/'edge_discovery_2026_09/batch2/native_engine.py').read_text()
src=src.replace('nr=0','nr=0;events=np.zeros((n,5))',1)
src=src.replace('px-=slip;g=', 'px-=slip;events[i,2]=qty;events[i,3]=px;g=')
src=src.replace('px=min(o[i],stop)-slip;g=', 'px=min(o[i],stop)-slip;events[i,2]=qty;events[i,3]=px;g=')
src=src.replace('k=4. if policy in (3,8)', 'events[i,0]=qty;events[i,1]=entry\n   k=4. if policy in (3,8)')
src=src.replace('unreal=(c[i]-entry)*mult*qty','events[i,4]=qty\n  unreal=(c[i]-entry)*mult*qty').replace('return path,records[:nr]','return path,records[:nr],events')
(P/'event_engine.py').write_text(src)
sys.path.insert(0,str(R));import run as ref
from event_engine import run
checks={}
for parent in ['baseline','ridge10','width20']:
 for case,fee,ticks in [('stress',7.,1),('severe',14.,2)]:
  parts=[];arrays={};masks=ref.translate(ref.masks[parent])
  for s,b in ref.nb.items():
   p,t,e=run(**ref.nd[s],signal=masks[s],fee=fee,slip=ref.old.TICK[s]*ticks)
   a=pd.DataFrame(e,index=b.index,columns=['entry_qty','entry_px','exit_qty','exit_px','ref_qty']);a['close']=b.close;a['net']=p[:,0];arrays[s]=a;parts.append(pd.Series(p[:,0],index=b.index))
  ix=parts[0].index.union(parts[1].index).union(parts[2].index);agg=sum(x.reindex(ix).ffill().fillna(0) for x in parts)
  prior=pd.read_parquet(R/f'path_native_{case}_{parent}.parquet');err=(agg-prior.net).abs().max();assert err<1e-7;checks[parent+'_'+case]=err
  out=pd.concat(arrays,axis=1).reindex(ix);out.to_parquet(P/f'core_events_{parent}_{case}.parquet');print(parent,case,'parity',err,flush=True)
(P/'core_event_parity.json').write_text(json.dumps(checks,indent=2))
