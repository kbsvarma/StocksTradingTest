from pathlib import Path
import pandas as pd,numpy as np,json,hashlib,time
from engine import compute
P=Path(__file__).resolve().parent
prices=np.load(P/'prices.npy');names=np.array(json.loads((P/'matrix_symbols.json').read_text()));cal=pd.to_datetime(json.loads((P/'calendar.json').read_text()));daynums=cal.to_numpy().astype('datetime64[D]').astype(np.int64)
pos=lambda s:int(np.flatnonzero(cal<=pd.Timestamp(s))[-1])
d0=pos('2021-12-31');mid=pos('2023-12-31');dend=pos('2025-12-31');cfend=len(cal)-1
aa,bb=np.triu_indices(len(names),1);total=len(aa);outdir=P/'all_pairs';outdir.mkdir(exist_ok=True)
labels=['days','years','cagr_0bps','cagr_5bps','cagr_10bps','hold_cagr_5bps','a_cagr_5bps','b_cagr_5bps','maxdd_5bps','hold_maxdd_5bps','annual_turnover','relative_log_5bps','relative_log_10bps','strategy_log_5bps','hold_log_5bps','correlation','diversification_log_bonus']
cols=['start_index','end_index']+[prefix+'_'+x for prefix in ['full','discovery','half1','half2','confirmation'] for x in labels]
metadata=dict(stocks=len(names),combinations=total,protocol_sha256=hashlib.sha256((P/'PROTOCOL.md').read_bytes()).hexdigest(),exclusions_sha256=hashlib.sha256((P/'ai_mag7_exclusions.csv').read_bytes()).hexdigest(),matrix_sha256=hashlib.sha256((P/'prices.npy').read_bytes()).hexdigest(),discovery_start=str(cal[d0].date()),discovery_end=str(cal[dend].date()),confirmation_end=str(cal[-1].date()))
(P/'search_metadata.json').write_text(json.dumps(metadata,indent=2));print(metadata,flush=True)
start=time.time();tested=0;rankable=0
for k in range(0,total,10000):
 stop=min(k+10000,total);res=compute(prices,aa[k:stop],bb[k:stop],daynums,d0,mid,dend,cfend)
 f=pd.DataFrame(res,columns=cols);f.insert(0,'b',names[bb[k:stop]]);f.insert(0,'a',names[aa[k:stop]])
 f['status']=np.where(f.full_days.notna(),'tested','insufficient_continuous_overlap');f['rankable']=f.discovery_days.notna()
 f['score']=f[['half1_strategy_log_5bps','half1_relative_log_5bps','half2_strategy_log_5bps','half2_relative_log_5bps']].min(axis=1,skipna=False)
 f['discovery_cagr_advantage']=f.discovery_cagr_5bps-f.discovery_hold_cagr_5bps
 f.to_parquet(outdir/f'part_{k:08d}.parquet',index=False)
 tested+=int(f.full_days.notna().sum());rankable+=int(f.rankable.sum())
 print('evaluated',stop,'/',total,'tested',tested,'rankable',rankable,'seconds',round(time.time()-start,1),flush=True)
metadata.update(tested=tested,insufficient_overlap=total-tested,rankable=rankable,seconds=time.time()-start)
(P/'search_metadata.json').write_text(json.dumps(metadata,indent=2));print('DONE',flush=True)
