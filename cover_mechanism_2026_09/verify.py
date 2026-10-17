from pathlib import Path
import numpy as np,json,pandas as pd
from options import hedge,option_crp,delta
P=Path(__file__).resolve().parent;checks={}
# A long-gamma hedge sells after the rise and buys back after the fall.
v=hedge(np.array([100.,110.,100.]),np.array([0.,-1.,0.]),1,0,0);assert abs(v[0]-10)<1e-9;checks['hedge_trade_direction_and_cash']=True
v=hedge(np.array([100.,110.,100.]),np.array([0.,-1.,0.]),0,0,0);assert v[0]==0;checks['entry_only_control']=True
bid=np.ones((10,2));ask=bid+.02;cap=np.ones((10,2))*100
v=option_crp(bid,ask,0,.65,True,1000,cap,cap);assert v[0]<0;checks['option_spreads_and_fees_reduce_constant_price_nav']=True
bid[5:]=0.;ask[5:]=.01;v=option_crp(bid,ask,1,.65,True,1000,cap,cap);assert v[0]>=-1000-1e-6;checks['zero_bid_fee_reserve_no_borrowing']=True
s=np.array([100.]);t=np.array([1/365]);sigma=.2
from scipy.special import ndtr
d1=.5*sigma*np.sqrt(t);d2=-d1;premium=s*(2*ndtr(d1)-1)-s*(2*ndtr(d2)-1);d,iv=delta(s,100.,t,premium);assert abs(iv[0]-sigma)<1e-8;checks['implied_volatility_recovers_known_price']=True
x=json.loads((P/'inventory_parent_parity.json').read_text());assert max(x.values())<1e-7;checks['six_futures_parent_replays_match']=True
# Observable prior marks, then fills: rebalance at elevated relative price before reversal.
b=np.array([[1.,1.],[1.,1.],[2.,.5],[2.,.5],[1.,1.],[1.,1.]])
a=b.copy();dynamic=option_crp(b,a,1,0.,False,10000.)[0];held=option_crp(b,a,0,0.,False,10000.)[0]
assert dynamic>held;checks['frictionless_relative_reversal_harvest']=True
caps=np.ones_like(b)*1000;caps[-1]=0
v=option_crp(b,a,0,0.,True,10000.,caps,caps);assert v[3]>0 and v[0]==-10000.;checks['unfilled_terminal_inventory_flagged_and_written_off']=True
b=np.ones((5,2));caps=np.ones_like(b)*1000;ex=np.ones((2,2));ec=np.ones((2,2))*25
v=option_crp(b,b,0,0.,True,10000.,caps,caps,ex,ec);assert abs(v[0])<1e-8 and v[3]==0;checks['staged_exit_cash_and_inventory_conservation']=True
aa=pd.read_csv(P/'futures_inventory_scores.csv');bb=pd.read_csv(P/'futures_gated_inventory_scores.csv');aa=aa[(aa.design=='parent')&(aa.period=='all')];bb=bb[(bb.design=='parent')&(bb.period=='all')];cc=aa.merge(bb,on=['parent','case']);assert (cc.net_x-cc.net_y).abs().max()<1e-7;checks['gated_inventory_parent_parity']=True
(P/'verification.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks))
