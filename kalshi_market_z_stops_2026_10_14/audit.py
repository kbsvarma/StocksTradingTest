"""Independent scalar arithmetic and read-only production preservation check."""
from pathlib import Path
import json,gzip,pickle,math,hashlib,subprocess
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
P=Path(__file__).resolve().parent;R=P.parent
rows=json.loads((P/'ledger.json').read_text());levels=json.loads((P/'thresholds.json').read_text())
d=pickle.load(gzip.open(P/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz','rb'))
actual={r['ticker']:r for r in d['actual']};orders={r['ticker']:r for r in json.loads((P/'orders.json').read_text())}
assert len(rows)==len(actual)==len(orders)==461
checks=0;fees=0
for x in rows:
    r=actual[x['ticker']];o=orders[x['ticker']]
    assert x['hold_net']==float(o['pnl']) and x['won']==int(o['side']==o['result'])
    assert D(o['filled'])*x['won']-D(o['cost'])-D(o['fees'])==D(o['pnl'])
    first=None;low=math.inf;start,sp=ss[r['asset']]
    for t in range(math.floor(r['entered'])+1,int(r['close'])):
        spot=float(sp[t-1-start])
        if r['asset'] in ['BTC','ETH','SOL','XRP']:spot=r['entry_spot']*spot/float(sp[int(r['t'])-1-start])
        z=r['side']*((spot-r['reference'])/r['reference'])/(r['entry_sigma']*math.sqrt((r['close']-t)/60))
        assert math.isfinite(z);low=min(low,z);checks+=1
        if first is None and levels[r['asset']] is not None and z<levels[r['asset']]:first=t
    assert first==x['trigger'] and abs(low-x['min_z'])<1e-7
    if first is not None:assert x['late']==(first+1>=r['close'])
    for name,y in x['scenarios'].items():
        if not y['exit']:continue
        px=D(str(y['price']));q=D(str(r['quantity']))
        f=(D('.07')*q*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
        cash=(q*px-f).quantize(D('.0001'),rounding=ROUND_FLOOR)
        basis=D(str(r['cost']))+D(str(r['entry_fee']))
        assert abs(float(cash-basis)-y['fees_only_net'])<1e-9
        assert abs(float(cash-basis-q*D('.01'))-y['net'])<1e-9
        assert abs(y['net']-r['hold_net']-y['delta'])<1e-9
        assert first+1<r['close'];fees+=1
summary=json.loads((P/'results.json').read_text())
for mode,groups in summary.items():
    allr=groups['all'];assert abs(allr['hold']+allr['loss_savings']-allr['winner_cost']-allr['net'])<1e-9
    assert abs(sum(x['hold'] for x in groups['assets'].values())-allr['hold'])<1e-9
    assert abs(sum(x['net'] for x in groups['assets'].values())-allr['net'])<1e-9
    assert abs(allr['fees_only_net']-allr['net']-allr['friction'])<1e-9
    assert allr['winners_cut']==0
base=json.loads((R/'kalshi_all_lane_exits_2026_10_14/preservation_after.json').read_text());live=R/'kalshi_revalidation_2026_09_12/live_v1'
code={n:hashlib.sha256((live/n).read_bytes()).hexdigest() for n in base['hashes']}
flags={n:hashlib.sha256((live/'state'/n).read_bytes()).hexdigest() for n in base['flags']}
services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in base['services']}
assert code==base['hashes'] and flags==base['flags'] and services==base['services']
result=dict(scalar_z_observations_checked=checks,independent_fee_calculations=fees,ledger_cash_identities=461,first_crossing_paths=461,all_winners_preserved_in_sample=True,live_code_unchanged=True,live_flags_unchanged=True,services_and_pids_unchanged=True,live_trading_actions=False)
(P/'independent_audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
