"""Offline execution/risk regression with assumed historical quote fills. No API client."""
from collections import Counter
from decimal import Decimal as D
import importlib.util
import json
from pathlib import Path
import pickle
import sys
import tempfile
from unittest.mock import patch
import engine
from lanes import Z2,Z3,Z3_ARM

P=Path(__file__).resolve().parent
R=P.parent/'kalshi_parallel_design_2026_10_02'
sys.path.insert(0,str(R))
from portfolio import etday
from research import metrics

class Exchange:
    def __init__(self):self.cash=D('50');self.current=None;self.posts=[]
    def positions(self):return []
    def orders(self,**kwargs):return []
    def balance(self):return self.cash
    def book(self,ticker):
        r=self.current
        return {'no_dollars' if r['side']==1 else 'yes_dollars':[[str(1-D(str(round(r['entry'],8)))),str(r['quantity'])]]}
    def submit(self,r):self.posts.append(r);return {'order_id':r['client_id']}

def run(module,rows,market_index):
    now=[0];ex=Exchange();closed=[];active=[];blocks=Counter()
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/Z3_ARM).write_text('5')
        store=module.Store(root/'orders.sqlite3');bot=module.Engine(ex,store,root,live=True,clock=lambda:now[0])
        def settle(t):
            nonlocal active
            left=[]
            for r in active:
                if r['settled']<=t:
                    old=store.get(r['ticker']);old.update(status='settled',pnl=str(round(r['pnl']-.01*r['quantity'],8)),settled_day=etday(r['settled']))
                    store.save(old);ex.cash+=D(str(r['quantity']*r['won']));closed.append(r)
                else:left.append(r)
            active=left
        with patch.object(module,'today',side_effect=lambda:etday(now[0])):
            for r in sorted(rows,key=lambda r:(r['entered'],0 if r['lane']==Z2 else 1,r['asset'])):
                now[0]=r['entered'];settle(now[0]);ex.current=r
                m=market_index[r['ticker']];f=next(f for f in m['features'] if f['t']==r['t'])
                s=dict(status='paper_fill',ticker=r['ticker'],asset=r['asset'],lane=r['lane'],side='yes' if r['side']==1 else 'no',signal_ask=f['ask'],signal_bid=f['bid'],z=f['z'],remaining_minutes=f['rem'],decision_ts=f['t'],close_ts=m['close'],exchange_index=2,price_ranges=m['ranges'])
                try:reason=bot.attempt(s)
                except ValueError as err:reason='validation: '+str(err)
                if reason!='ack':blocks[reason]+=1;continue
                saved=store.get(r['ticker']);assert int(saved['requested_count'])==r['quantity'];saved.update(status='open',filled=str(r['quantity']));store.save(saved)
                ex.cash-=D(str(round(r['quantity']*r['entry']+r['fee']+.01*r['quantity'],8)))
                active.append(r)
        settle(float('inf'));store.db.close()
    return sorted(closed,key=lambda r:(r['close'],r['asset'])),dict(blocks)

def main():
    market_index={m['ticker']:m for m in pickle.loads((R/'features.pickle').read_bytes())}
    inputs=json.loads((P/'shared_replay_inputs.json').read_text())
    base=[dict(r,lane=Z2) for r in inputs['current_signal95']]
    premium=[dict(r,lane=Z3) for r in inputs['premium_z3']]
    spec=importlib.util.spec_from_file_location('original_engine',P.parent/'kalshi_capacity20_2026_10_02/engine.py');original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    before,_=run(original,base,market_index);after,_=run(engine,base,market_index)
    assert [r['ticker'] for r in before]==[r['ticker'] for r in after]
    assert metrics(before)==metrics(after)
    joint,blocks=run(engine,base+premium,market_index)
    out=dict(z2_contracts=5,z3_contracts=5,shared_limits=dict(contracts=20,exposure=19,daily=10),period='September 12–October 1 UTC',scenario='Assumed next-minute opening quote fill, modeled fees plus 1c/contract, actual staged engine risk checks',
        z2_regression='IDENTICAL market list and P&L with z3 absent',current5=metrics(before),joint=metrics(joint),
        by_lane={n:metrics([r for r in joint if r['lane']==n]) for n in (Z2,Z3)},
        displaced_z2=len({r['ticker'] for r in before}-{r['ticker'] for r in joint if r['lane']==Z2}),blocked=blocks,
        limitations='No historical September orderbook depth or fill assurance. Does not model network latency, failed IOC attempts, or within-minute retry timing. Historical exploratory evidence, not future profit.')
    (P/'deployment_replay.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))

if __name__=='__main__':main()
