from pathlib import Path
import numpy as np,pandas as pd,json
from round_c import inputs,replay
from round_g import expert_weights
P=Path(__file__).resolve().parent;prices=pd.read_parquet(P.parent/'cover_validation_2026_09/long_eligible_prices.parquet');ix,ev,cl,ref,di,ex=inputs('baseline','stress',prices,'2018-05-01','2026-07-02');flags=np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]];loc=np.flatnonzero(di>=0);end=loc[500];n=501;sp=prices.loc[ix,['CI','YUMC']].to_numpy();checks={}
kw=dict(capital=100000.,mode=2,integer=True,fee=7.,slipticks=1.,stockfee=.001,w=.5,resize_active=False,margin_rate=.3)
full,*_=replay(ev,cl,ref,di,ex,sp,flags,**kw);cut,*_=replay(ev[:end+1],cl[:end+1],ref[:end+1],di[:end+1],ex[:end+1],sp[:n],flags[:n],**kw);assert np.allclose(full[:n-1,0],cut[:-1,0]);checks['frozen_candidate_causal_prefix']=True
for shift in [1.,3.]:
 z,*_=replay(ev,cl,ref,di,ex,sp*shift,flags,**kw);assert np.allclose(z,full)
checks['stock_unit_invariance']=True
expert=expert_weights(ref[loc],sp.mean(axis=1),.002);short=expert_weights(ref[loc][:n],sp[:n].mean(axis=1),.002);assert np.array_equal(expert[:n],short);checks['universal_expert_prefix']=True
# No active futures resizing beyond the initial position establishment.
_,_,changes,*_=replay(ev,cl,ref,di,ex,sp,flags,**kw);assert changes==np.sum(ev[0,:,4]);checks['no_active_trend_resizes']=True
# Compare exact recorded final prototype with the direct frozen replay.
d=pd.read_parquet(P/'round_g_paths.parquet')['baseline_stress_CI_YUMC__weekly_entry'];assert np.allclose(d.to_numpy()*100000,full[:,0]);checks['final_artifact_replay_parity']=True
(P/'candidate_verification.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks))
