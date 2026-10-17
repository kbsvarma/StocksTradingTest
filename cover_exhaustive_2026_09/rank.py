from pathlib import Path
import json,hashlib
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent
meta=json.loads((P/'search_metadata.json').read_text());assert hashlib.sha256((P/'PROTOCOL.md').read_bytes()).hexdigest()==meta['protocol_sha256'];assert hashlib.sha256((P/'ai_mag7_exclusions.csv').read_bytes()).hexdigest()==meta['exclusions_sha256']
frames=[];counts=dict();ex=set(pd.read_csv(P/'ai_mag7_exclusions.csv').symbol)
for file in sorted((P/'all_pairs').glob('*.parquet')):
 f=pd.read_parquet(file);f=f[f.rankable].copy();f['non_ai']=~f.a.isin(ex)&~f.b.isin(ex);frames.append(f)
f=pd.concat(frames,ignore_index=True);f=f.sort_values(['score','discovery_cagr_advantage','a','b'],ascending=[False,False,True,True]);f['discovery_rank_all']=np.arange(1,len(f)+1)
# Every discovery-ranked pair is retained, including negative scores and later failures.
f.to_parquet(P/'all_ranked_pairs.parquet',index=False)
selected=[]
for universe,d in [('all_stocks',f),('without_ai_mag7',f[f.non_ai])]:
 top=d.head(10).copy();top.insert(0,'list',universe);top.insert(1,'rank',np.arange(1,len(top)+1));top['pair']=top.a+' / '+top.b
 top['confirmation_return_5bps']=np.expm1(np.log1p(top.confirmation_cagr_5bps)*top.confirmation_years)
 top['confirmation_hold_return_5bps']=np.expm1(np.log1p(top.confirmation_hold_cagr_5bps)*top.confirmation_years)
 top['confirmation_end']=pd.Timestamp('2025-12-31')+pd.to_timedelta((top.confirmation_years*365.25).round(),unit='D')
 top['confirmation_complete']=top.confirmation_end==pd.Timestamp(meta['confirmation_end'])
 top['confirmation_basic_pass']=top.confirmation_complete & (top.confirmation_return_5bps>0)&(top.confirmation_relative_log_5bps>0)&(top.confirmation_relative_log_10bps>0)
 top.to_csv(P/f'top10_{universe}.csv',index=False);selected.append(top)
 complete=(pd.Timestamp('2025-12-31')+pd.to_timedelta((d.confirmation_years*365.25).round(),unit='D'))==pd.Timestamp(meta['confirmation_end'])
 baseline=complete&(d.confirmation_cagr_5bps>0)&(d.confirmation_relative_log_5bps>0)&(d.confirmation_relative_log_10bps>0)
 counts[universe]=dict(confirmation_complete_pairs=int(complete.sum()),confirmation_basic_passes_all_pairs=int(baseline.sum()),rankable=len(d),positive_conservative_score=int((d.score>0).sum()),selected=len(top),confirmation_basic_passes=int(top.confirmation_basic_pass.sum()))
 pd.DataFrame(d.head(100)).to_csv(P/f'top100_{universe}.csv',index=False)
 print(universe);print(top[['rank','pair','score','discovery_cagr_5bps','discovery_hold_cagr_5bps','confirmation_return_5bps','confirmation_hold_return_5bps','confirmation_basic_pass']].to_string(index=False))
pd.concat(selected,ignore_index=True).to_csv(P/'two_lists.csv',index=False)
(P/'ranking_counts.json').write_text(json.dumps(counts,indent=2))
# Separate full-history hindsight leaderboard, explicitly exploratory and different dates.
full=[]
for file in sorted((P/'all_pairs').glob('*.parquet')):
 d=pd.read_parquet(file,columns=['a','b','full_cagr_5bps','full_hold_cagr_5bps','full_years','full_maxdd_5bps','start_index','end_index']);d['advantage']=d.full_cagr_5bps-d.full_hold_cagr_5bps;full.append(d.nlargest(100,'advantage'))
pd.concat(full).nlargest(100,'advantage').to_csv(P/'retrospective_full_history_leaders_NOT_SELECTION.csv',index=False)
