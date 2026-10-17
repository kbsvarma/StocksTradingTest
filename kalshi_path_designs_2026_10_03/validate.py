"""Raw-data signal parity, offline real-engine risk parity and fill sensitivity."""
from impulse import *
import designs,importlib.util,tempfile
from decimal import Decimal as D
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('path_signal',P/'candidate_signal.py');sig=importlib.util.module_from_spec(spec);spec.loader.exec_module(sig)
designs.gate=eligible
release=ROOT/'kalshi_execution_improvements_2026_10_03/candidate';sys.path.insert(0,str(release));import engine,lanes

def actual(data,c,co,qty):
    rec,_,_=data;a=select(data,c,co);now=[0.];cash=D('50');active=[];done=[]
    lanes.LANES['third']=lanes.Lane('third',qty,D(str(c['floor'])),D('.95'),c['z'],D('.939'),D('.95'))
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/lanes.Z3_ARM).write_text('5');store=engine.Store(root/'orders.sqlite3');bot=engine.Engine(object(),store,root,live=True,clock=lambda:now[0])
        with patch.object(engine,'today',side_effect=lambda:s.etday(now[0])):
            for ar in a:
                now[0]=float(ar[s.ENTER]);left=[]
                for p in active:
                    if p['settled']<=now[0]:
                        record=store.get(p['ticker']);record.update(status='settled',pnl=str(round(p['pnl']-.01*p['quantity'],8)),settled_day=s.etday(p['settled']));store.save(record);cash+=D(str(p['quantity']*p['won']));done.append(p)
                    else:left.append(p)
                active=left;lane=int(ar[s.LANE]);n=qty if lane==2 else 5
                r=dict(rec[int(ar[s.OID])],lane=[lanes.Z2,lanes.Z3,'third'][lane],exchange_index=2,quantity=n)
                if bot.entry_guard(r,{},[],cash):continue
                r['pnl']=n*(r['won']-r['entry'])-r['fee'];store.save(dict(r,status='open',filled=str(n),requested_count=str(n)));cash-=D(str(round(n*r['entry']+r['fee']+.01*n,8)));active.append(r)
        done+=active;store.db.close()
    return sorted(done,key=lambda r:(r['close'],r['asset']))

def main():
    c=next(c for c in configurations() if c['id']==801);markets=impulse_features(pickle.loads((P/'features.pickle').read_bytes()));look={(m['ticker'],f['t']):f for m in markets for f in m['features']};out={'production_risk':{}}
    for qty in [5,1,2]:
        cache=pickle.loads((P/('five_contract_inputs.pickle' if qty==5 else f'inputs_size{qty}.pickle')).read_bytes())
        for rec,a,fs in cache.values():
            for oid in fs:fs[oid]=look[rec[oid]['ticker'],rec[oid]['t']]
        for source,period in [('quote','later'),('book','validation')]:
            data=cache[source,.85,qty];aa=actual(data,c,period,qty);rr=run(data,c,period,quantity=qty)
            assert [r['ticker'] for r in aa]==[r['ticker'] for r in rr]
            assert s.old.metrics(aa)==s.old.metrics(rr)
            out['production_risk'][str(qty)+'_'+source]=s.old.metrics(rr)
        if qty==5:
            data=cache['quote',.85,5];deltas=[]
            for seed in range(100):
                b=run(data,period='later',miss_rate=.25,miss_seed=seed);j=run(data,c,'later',miss_rate=.25,miss_seed=seed);deltas.append(compare(j,b)['delta_net'])
            out['miss25pct']=dict(mean=float(np.mean(deltas)),p05=float(np.quantile(deltas,.05)),p95=float(np.quantile(deltas,.95)),positive=sum(d>0 for d in deltas),scenarios=len(deltas))
    bars={a:{} for a in ['BTC','ETH']}
    for folder in s.FOLDERS:
        for asset in bars:
            for line in (folder/f'spot_{asset}.jsonl').open():
                for x in json.loads(line)['rows']:bars[asset][int(x[0])]=float(x[4])
    checked=qualified=0;maxz=maximp=0.
    for m in markets:
        if m['asset'] not in bars:continue
        fs={f['t']:f for f in m['features']}
        for f in m['features']:
            if not 5<=f['rem']<=7:continue
            q=s.old.quote(m['candles'].get(f['t']));prev=s.old.quote(m['candles'].get(f['t']-60))
            got=sig.decision(m['asset'],f['t'],m['start'],m['close'],q,prev,bars[m['asset']]);expected=eligible(c,f,m['asset']) and f['ask']-f['bid']<=.02000001
            assert got['eligible']==expected,(m['ticker'],f,got)
            if 'z' in got:
                maxz=max(maxz,abs(got['z']-f['z']));maximp=max(maximp,abs(got['impulse1']-f['impulse1']))
                assert maxz<1e-8 and maximp<1e-8
            checked+=1;qualified+=expected
    out['pure_signal_parity']=dict(checked=checked,qualified=qualified,max_z_error=maxz,max_impulse_error=maximp)
    sig_spec=dict(sig.SPEC,selected_id=801,selection='secondary exploratory family leader; primary failed',live_promotion=False)
    save('candidate_research_spec.json',sig_spec);save('validation.json',out);print(json.dumps(out,indent=2))

if __name__=='__main__':main()
