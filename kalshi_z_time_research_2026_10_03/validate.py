"""Independent production-risk regression and causality/boundary checks, offline."""
from study import *
from decimal import Decimal as D
import dataclasses, importlib.util, statistics, tempfile
from unittest.mock import patch

RELEASE=ROOT/'kalshi_execution_improvements_2026_10_03/candidate'
if not (RELEASE/'engine.py').exists():RELEASE=P/'production_snapshot'
sys.path.insert(0,str(RELEASE))
import engine, lanes

def actual_guard_replay(records,a,c=None):
    now=[0.];cash=D('50');active=[];done=[];blocks=collections.Counter()
    pro=c['profile'] if c else 0;r=PROFILES[pro]
    # Add a hypothetical lane ONLY to this in-memory offline import; source untouched.
    lanes.LANES['third']=lanes.Lane('third',5,D(str(r.lo)),D(str(r.hi)),0,D(str(r.hardcap)),D('.95') if pro==0 else D('1'))
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/lanes.Z3_ARM).write_text('5')
        store=engine.Store(root/'orders.sqlite3');bot=engine.Engine(object(),store,root,live=True,clock=lambda:now[0])
        with patch.object(engine,'today',side_effect=lambda:etday(now[0])):
            for x in a:
                lane=int(x[LANE]);prof=int(x[PROFILE])
                if lane==2:
                    if c is None or prof!=c['profile'] or not ((1<<int(x[AID])) & c['mask']) or not c['lo']<=x[REM]<=c['hi']:continue
                    if x[Z]<max(.5,c['z']+c['slope']*math.log2(x[REM]/5)):continue
                now[0]=float(x[ENTER]);left=[]
                for p in active:
                    if p['settled']<=now[0]:
                        saved=store.get(p['ticker']);saved.update(status='settled',pnl=str(round(p['pnl']-.05,8)),settled_day=etday(p['settled']));store.save(saved)
                        cash+=D(str(5*p['won']));done.append(p)
                    else:left.append(p)
                active=left
                r=dict(records[int(x[OID])],lane=[lanes.Z2,lanes.Z3,'third'][lane],exchange_index=2)
                reason=bot.entry_guard(r,{},[],cash)
                if reason:blocks[reason]+=1;continue
                store.save(dict(r,status='open',filled='5',requested_count='5'))
                cash-=D(str(round(5*r['entry']+r['fee']+.05,8)));active.append(r)
        done.extend(active);store.db.close()
    return sorted(done,key=lambda r:(r['close'],r['asset'])),dict(blocks)

def boundaries():
    # Candidate signals at exact time/z boundaries, future settlement blocking, shared daily reserve.
    def row(mid,asset,t,rem,z,price=.95,won=1,lane=2):
        return [mid,asset,1,lane,t,t+300,t+200,1,1,rem,z,price,.975,won,fee(price,5),mid]
    a=np.array([row(0,0,100,2,2.75),row(1,1,101,5,2.75),row(2,2,102,3,9),row(3,3,103,1,9),row(4,3,104,6,9),row(5,3,105,3,2.749999),row(6,0,500,3,2.75)],float)
    ids=simulate(a,1,2.75,2,5,15,0.)
    assert list(ids)==[0,1,6],ids # third simultaneous candidate blocked by daily risk; cash returns only on settlement.
    dup=np.array([row(0,0,100,3,3,lane=1),row(0,0,100,3,3,lane=2)],float)
    assert list(simulate(dup,1,2.75,2,5,15,0.))==[0]
    return dict(boundary_z_inclusive=True,time_bounds_inclusive=True,shared_daily_risk_blocks_third_simultaneous=True,settlement_releases_capacity=True,one_market_owner=True)

def main():
    records,a=pickle.loads((P/'quote_opportunities.pickle').read_bytes());cs={c['id']:c for c in json.loads((P/'expanded_shortlist_frozen.json').read_text())['candidates']}
    result=dict(boundaries=boundaries(),production_guard={})
    for cid in [None,25301,9220,7214]:
        c=cs[cid] if cid else None;aa=subset(a,'later');actual,blocks=actual_guard_replay(records,aa,c);fast=selected_rows(records,aa,c)
        assert [r['ticker'] for r in actual]==[r['ticker'] for r in fast],cid
        assert old.metrics(actual)==old.metrics(fast),(cid,old.metrics(actual),old.metrics(fast))
        result['production_guard'][str(cid)]=dict(exact_market_and_metrics_match=True,metrics=old.metrics(actual),blocks=blocks)
        print('production guard parity',cid,old.metrics(actual),flush=True)
    markets=pickle.loads((P/'features.pickle').read_bytes());bb={a:{} for a in ASSETS}
    for folder in FOLDERS:
        for a in ASSETS:
            for line in (folder/f'spot_{a}.jsonl').open():
                for r in json.loads(line)['rows']:bb[a][int(r[0])]=float(r[4])
    checked=0;maxdiff=0.
    for m in markets:
        for f in m['features']:
            # Every added minute14 plus deterministic controls, use production arithmetic.
            if f['rem']!=14 and f['t']//60%101:continue
            T=f['t'];raw={t:bb[m['asset']][t] for t in range(T-3660,T,60)};raw[T]=1e90
            vals=[raw[t] for t in range(T-3660,T,60)]
            sig=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]));z=f['side']*(vals[-1]/raw[m['start']-60]-1)/(sig*math.sqrt(f['rem']))
            maxdiff=max(maxdiff,abs(z-f['z']));assert abs(z-f['z'])<1e-8
            checked+=1
    result['independent_arithmetic']=dict(checked=checked,max_z_difference=maxdiff,forming_bar_excluded=True)
    result['production_sources']={p.name:digest(p) for p in RELEASE.glob('*.py') if p.name in json.loads((RELEASE/'staged_hashes.json').read_text())}
    save('validation.json',result);print('PASS',checked,maxdiff,flush=True)

if __name__=='__main__':main()
