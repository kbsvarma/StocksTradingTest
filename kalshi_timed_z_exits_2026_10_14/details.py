"""Selected-rule audit, ablation, chronological check and recorded-depth replay."""
from pathlib import Path
import json,gzip,pickle,math,bisect,importlib.util,datetime,collections,hashlib
import numpy as np
import grid as g
P=g.P;R=g.R
def scalar_threshold(c,r,remaining):
    f=c['family'];v=c['level']
    if f=='hold':return -math.inf
    if f=='floors':return g.prev.LEVELS[r['asset']] if g.prev.LEVELS[r['asset']] is not None else -math.inf
    if f=='drop':return r['entry_z']-v
    if f in ['late_tighter','late_looser']:
        change=.5 if remaining<120 else .25 if remaining<=300 else 0
        return v+change if f=='late_tighter' else v-change
    return v
def scalar_first(r,c,times,values):
    since=None
    for j,(t,z) in enumerate(zip(times,values)):
        age=t-r['entered'];rem=r['close']-t;w=c['window'];eligible=age>=c['grace']
        if w=='before_last60':eligible&=rem>=60
        elif w=='before_last120':eligible&=rem>=120
        elif w=='last300':eligible&=rem<=300
        elif w=='last120':eligible&=rem<=120
        elif w=='first120':eligible&=age<=120
        elif w=='first300':eligible&=age<=300
        if not eligible or not math.isfinite(z) or z>=scalar_threshold(c,r,rem):since=None;continue
        if since is None:since=t
        if t-since>=c['persist']:return j
    return -1
def record(r,c,p,mode='low',extra_friction=0):
    j=scalar_first(r,c,p['t'],p['z']);x={k:r[k] for k in ['ticker','asset','lane','won','entered','close','hold_net','quantity','cost','entry_fee']}
    x.update(config=c,trigger=int(p['t'][j]) if j>=0 else None,trigger_z=float(p['z'][j]) if j>=0 else None,exit=False,net=r['hold_net'],fee=0.,friction=0.,price=None)
    if j>=0 and p[mode]['fill'][j]:
        x.update(exit=True,net=float(p[mode]['net'][j])-extra_friction*r['quantity'],fee=float(p[mode]['fee'][j]),friction=float(p[mode]['friction'][j])+extra_friction*r['quantity'],price=float(p[mode]['price'][j]))
    x.update(delta=x['net']-r['hold_net'],fees_only_net=x['net']+x['friction']);return x
def metrics(xs):
    w=[x for x in xs if x['won']];l=[x for x in xs if not x['won']]
    return dict(trades=len(xs),wins=len(w),losses=len(l),hold=sum(x['hold_net'] for x in xs),net=sum(x['net'] for x in xs),delta=sum(x['delta'] for x in xs),loss_savings=sum(x['delta'] for x in l),losses_reduced=sum(x['delta']>1e-8 for x in l),winners_cut=sum(x['exit'] for x in w),winner_cost=-sum(x['delta'] for x in w),winners_turned_loss=sum(x['net']<0 for x in w),exit_fees=sum(x['fee'] for x in xs),friction=sum(x['friction'] for x in xs),fees_only_net=sum(x['fees_only_net'] for x in xs),triggered_winners=sum(x['trigger'] is not None for x in w))
def split_metrics(xs):
    return {period:metrics([x for x in xs if cond(x)]) for period,cond in [('full',lambda x:True),('development',lambda x:x['close']<g.SPLIT),('later',lambda x:x['close']>=g.SPLIT)]}
def bootstrap(xs):
    tz=datetime.timezone(datetime.timedelta(hours=-4));start=datetime.date(2026,9,12);end=datetime.date(2026,10,14);days=[(start+datetime.timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    by=collections.defaultdict(float)
    for x in xs:by[datetime.datetime.fromtimestamp(x['close'],tz).date().isoformat()]+=x['delta']
    vv=np.array([by[d] for d in days]);rng=np.random.default_rng(20261015);out=[]
    for _ in range(5000):
        ix=rng.integers(0,len(vv),size=(len(vv)+1)//2);indices=np.ravel(np.column_stack((ix,(ix+1)%len(vv))))[:len(vv)];out.append(vv[indices].sum())
    return dict(incremental_pnl_interval95=np.quantile(out,[.025,.975]).tolist(),method='Exploratory 2-day block bootstrap, all33calendar days, not adjusted for parameter selection; not proof of an edge.',daily=dict(by))
def depth_record(r,c,rep,raw):
    p=rep.prepare(r,raw['bundles'][r['ticker']],raw['prices'][r['asset']]);t=sorted(p['z']);z=[p['z'][k].get('5hz',math.nan) for k in t];j=scalar_first(r,c,t,z)
    x={k:r[k] for k in ['ticker','asset','lane','won','entered','close','hold_net','quantity','cost','entry_fee']}
    x.update(config=c,trigger=t[j] if j>=0 else None,trigger_z=z[j] if j>=0 else None,exit=False,net=r['hold_net'],delta=0.,fee=0.,friction=0.,price=None,reason='no signal')
    if j>=0:
        at=t[j];bt=p['bt'];b=p['b']['books'];si=3 if r['side']==1 else 4;bi=bisect.bisect_right(bt,at*1000)-1;ai=bisect.bisect_left(bt,(at+1)*1000)
        x['reason']='no timely paired books'
        if bi>=0 and ai<len(b) and at*1000-bt[bi]<=500 and bt[ai]-(at+1)*1000<=500 and bt[ai]<r['close']*1000:
            before=rep.sale(b[bi][si],r['quantity']);after=rep.sale(b[ai][si],r['quantity']);x['reason']='insufficient depth'
            if before is not None and after is not None:
                fill=min([before,after],key=lambda v:v['proceeds']);net=fill['proceeds']-.01*r['quantity']-r['cost']-r['entry_fee']
                x.update(exit=True,net=net,delta=net-r['hold_net'],fee=fill['fee'],friction=.01*r['quantity'],price=fill['gross']/r['quantity'],reason='paired full-quantity books',book_before=bt[bi]/1000,book_after=bt[ai]/1000,levels=fill['levels'])
    x['fees_only_net']=x['net']+x['friction'];return x
def main():
    d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=json.loads((P/'configs.json').read_text());sel=json.loads((P/'selection.json').read_text());arr=dict(np.load(P/'grid_arrays.npz'));index={c['id']:k for k,c in enumerate(cc)}
    paths={r['ticker']:g.prepare(r,d,ss) for r in rr};paths3={r['ticker']:g.prepare(r,d,ss,3) for r in rr}
    maps={name:{a:cc[index[name]] for a in g.ASSETS} for name in ['hold','previous_market_floors','z0.5_g0_p0_all','z1_g0_p0_all','z1_g30_p10_all','z1.5_g30_p10_all']}
    for key,v in sel['common'].items():maps['development_common_'+key]={a:v['config'] for a in g.ASSETS}
    for key,v in sel['asset_specific'].items():maps['development_asset_'+key]={a:x['config'] for a,x in v['choices'].items()}
    # Matched ablations isolate the effect of timing without changing selected levels.
    base=maps['development_asset_0']
    for variant in ['no_time_rules','no_confirmation','no_minimum_hold','no_exit_window','z_plus025','z_minus025']:
        changed={a:dict(c) for a,c in base.items()}
        for a,c in changed.items():
            if c['family']=='hold':continue
            if variant in ['no_time_rules','no_confirmation']:c['persist']=0
            if variant in ['no_time_rules','no_minimum_hold']:c['grace']=0
            if variant in ['no_time_rules','no_exit_window']:c['window']='all'
            if variant=='z_plus025':c['level']+=.25
            if variant=='z_minus025':c['level']-=.25
            c['id']+='__'+variant
        maps['zero_cut_map_'+variant]=changed
    outputs={};ledgers={};validation_checks=0
    for name,mapping in maps.items():
        xs=[record(r,mapping[r['asset']],paths[r['ticker']]) for r in rr];ledgers[name]=xs
        for j,x in enumerate(xs):
            cid=x['config']['id']
            if cid in index:
                k=index[cid];assert abs(arr['net_low'][k,j]-x['net'])<1e-9
                assert int(arr['trigger'][k,j])==(x['trigger'] if x['trigger'] is not None else -1);validation_checks+=1
        outputs[name]=dict(rules=mapping,periods=split_metrics(xs),assets={a:metrics([x for x in xs if x['asset']==a]) for a in g.ASSETS},lanes={a:metrics([x for x in xs if x['lane']==a]) for a in ['cushion_z2','premium_z3']})
        if name in ['development_asset_0','development_asset_0.02','development_common_0']:
            outputs[name]['sensitivity']={mode:split_metrics([record(r,mapping[r['asset']],paths3[r['ticker']] if mode=='latency3s' else paths[r['ticker']],mode='high' if mode=='minute_high' else 'low',extra_friction=.01 if mode=='friction2c' else 0) for r in rr]) for mode in ['latency3s','friction2c','minute_high']}
            outputs[name]['uncertainty']=bootstrap(xs)
    # Verify random grid cells with an independent scalar confirmation clock.
    rng=np.random.default_rng(20261015)
    for _ in range(2000):
        k=int(rng.integers(len(cc)));j=int(rng.integers(len(rr)));r=rr[j];x=record(r,cc[k],paths[r['ticker']]);assert abs(x['net']-arr['net_low'][k,j])<1e-9;assert (x['trigger'] if x['trigger'] is not None else -1)==arr['trigger'][k,j];validation_checks+=1
    g.save('detailed_results.json',outputs)
    with gzip.open(P/'selected_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
    source=R/'kalshi_fast_z_exit_2026_10_14';sp=importlib.util.spec_from_file_location('recorded_replay',source/'replay.py');rep=importlib.util.module_from_spec(sp);sp.loader.exec_module(rep);raw=pickle.load(gzip.open(source/'inputs.pkl.gz','rb'))
    depth={}
    for name in ['hold','previous_market_floors','development_asset_0','development_asset_0.02','development_asset_0.05','development_common_0','z1_g30_p10_all']:
        xs=[depth_record(r,maps[name][r['asset']],rep,raw) for r in raw['cohorts']['actual']];depth[name]=dict(metrics=metrics(xs),ledger=xs)
    g.save('recorded_depth.json',depth)
    g.save('scalar_audit.json',dict(scalar_grid_matches=validation_checks,main_grid_configurations=len(cc),actual_trades=len(rr),full_source_paths=len(paths),selection_excludes_future_fitted_market_floors=True,quote_low_main=True,full_history_depth_available=False))
    print('scalar checks',validation_checks,flush=True)
    for name in ['development_asset_0','development_asset_0.02','zero_cut_map_no_time_rules','zero_cut_map_no_confirmation','zero_cut_map_no_minimum_hold','zero_cut_map_no_exit_window','zero_cut_map_z_plus025','zero_cut_map_z_minus025']:
        print(name,json.dumps(outputs[name]['periods']),flush=True)
    print('DEPTH',json.dumps({k:v['metrics'] for k,v in depth.items()}),flush=True)
if __name__=='__main__':main()
