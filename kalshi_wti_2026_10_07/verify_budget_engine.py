"""Cross-check selected portfolio replay against staged entry_guard, offline only."""
import gzip,json,sys,tempfile
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
import engine
from lanes import NEW_ASSETS,BASE_NONCRYPTO_ASSETS,NONCRYPTO_ARM,WTI_ARM,Z3_ARM,Z2
import budget_replay as research

def guard(rows,stress):
    now=[0];active=[];closed=[];cash={0:D(50),2:D(50)}
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/NONCRYPTO_ARM).write_text(json.dumps(dict(version=1,contracts=5,assets=list(BASE_NONCRYPTO_ASSETS))))
        (root/WTI_ARM).write_text('5');(root/Z3_ARM).write_text('5')
        store=engine.Store(root/'offline.sqlite3');bot=engine.Engine(object(),store,root,live=True,clock=lambda:now[0])
        def settle(t):
            nonlocal active
            left=[]
            for row in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
                if row['settled']<=t:
                    item=store.get(row['ticker']);item.update(status='settled',pnl=str(round(row['net'],8)),settled_day=research.s.r.etday(row['settled']));store.save(item)
                    cash[research.route(row['asset'])]+=D(row['won']*5);closed.append(row)
                else:left.append(row)
            active=left
        with patch.object(engine,'today',side_effect=lambda:research.s.r.etday(now[0])):
            for row in sorted(rows,key=lambda r:(r['entered'],r['lane']!=Z2,research.ASSETS.index(r['asset']))):
                now[0]=row['entered'];settle(now[0]);route=research.route(row['asset'])
                signal=dict(row,exchange_index=route,decision_ts=row['t'],close_ts=row['close'])
                reason=bot.entry_guard(signal,{},[],cash[route])
                if reason:continue
                store.save(dict(signal,status='open',requested_count='5',filled='5'))
                cash[route]-=D(str(round(5*row['entry']+row['fee']+5*stress,8)))
                active.append(dict(row,net=row['pnl']-5*stress))
        settle(float('inf'));store.db.close()
    return sorted(closed,key=lambda r:(r['settled'],r['ticker']))

with gzip.open(P/'budget_opportunities.json.gz','rt') as f:pools=json.load(f)
out={}
for scenario,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
    rows=[r for r in pools[scenario] if research.s.r.ts('2026-09-23T00:00:00Z')<=r['close']<research.s.r.SPLIT]
    expected,_=research.simulate(rows,budget=15,exposure=19,stress=stress)
    actual=guard(rows,stress);key=lambda r:(r['ticker'],r['lane'],r['t'],r['entry'])
    assert [key(x) for x in expected]==[key(x) for x in actual],scenario
    assert abs(sum(x['net'] for x in expected)-sum(x['net'] for x in actual))<1e-7
    out[scenario]=dict(passed=True,**research.summary(actual))
(P/'budget_engine_parity.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
