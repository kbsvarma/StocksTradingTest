"""Vectorized fixed-entry exit research. Offline; isolated experiment outputs."""
from pathlib import Path
import json,gzip,pickle,math,importlib.util,hashlib,datetime
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent;PREV=R/'kalshi_market_z_stops_2026_10_14'
spec=importlib.util.spec_from_file_location('previous_fixed_levels',PREV/'replay.py');prev=importlib.util.module_from_spec(spec);spec.loader.exec_module(prev)
SPLIT=datetime.datetime.fromisoformat('2026-10-12T00:00:00-04:00').timestamp()
ASSETS=list(prev.LEVELS)
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def configs():
    out=[dict(id='hold',family='hold',level=0.,grace=0,persist=0,window='all'),dict(id='previous_market_floors',family='floors',level=0.,grace=0,persist=0,window='all')]
    for z in [-1,-.5,0,.25,.5,.75,1,1.25,1.5,1.75,2,2.25,2.5,2.75,3]:
        for g in [0,15,30,60,120]:
            for p in [0,2,5,10,20,30,60]:
                for w in ['all','before_last60','before_last120','last300','last120','first120','first300']:
                    if w=='first120' and g>=120:continue
                    out.append(dict(id=f'z{z:g}_g{g}_p{p}_{w}',family='absolute',level=z,grace=g,persist=p,window=w))
    for f in ['late_tighter','late_looser']:
        for z in [0,.5,1,1.5,2,2.5]:
            for g in [0,30,60]:
                for p in [0,5,10,30]:
                    out.append(dict(id=f'{f}_z{z:g}_g{g}_p{p}',family=f,level=z,grace=g,persist=p,window='all'))
    for drop,g,p in [(.25,0,0),(.25,0,10),(.5,0,0),(.5,0,10),(1,0,0),(1,0,10),(2,120,60)]:
        out.append(dict(id=f'drop{drop:g}_g{g}_p{p}',family='drop',level=drop,grace=g,persist=p,window='all'))
    assert len({c['id'] for c in out})==len(out)
    return out
def prepare(r,d,ss,latency=1):
    t,z=prev.path(r,ss);m=d['markets'][r['ticker']];valid=t+latency<r['close'];ends=(t+latency)//60*60+60
    ans=dict(t=t,z=z,age=t-r['entered'],remaining=r['close']-t,valid=valid)
    for mode,suffix in [('low','l'),('high','h')]:
        cache={int(e):prev.held_quote(m,int(e),r['side'],suffix) for e in set(ends)}
        price=np.array([cache[int(e)] if cache[int(e)] is not None else np.nan for e in ends])
        assert np.isfinite(price[valid]).all()
        net=np.full(len(t),r['hold_net']);fee=np.zeros(len(t));fr=np.zeros(len(t));filled=valid&(price>0)
        for px in set(price[filled]):
            sell=prev.sell(r['quantity'],float(px));ix=filled&(price==px);net[ix]=sell['cash']-r['cost']-r['entry_fee'];fee[ix]=sell['fee'];fr[ix]=sell['friction']
        ans[mode]=dict(price=price,net=net,fee=fee,friction=fr,fill=filled)
    return ans
def thresholds(c,r,p):
    f=c['family'];base=c['level']
    if f=='hold':return np.full(len(p['t']),-np.inf)
    if f=='floors':return np.full(len(p['t']),prev.LEVELS[r['asset']] if prev.LEVELS[r['asset']] is not None else -np.inf)
    if f=='drop':base=r['entry_z']-base
    if f in ['late_tighter','late_looser']:
        change=np.where(p['remaining']<120,.5,np.where(p['remaining']<=300,.25,0.))
        return base+(change if f=='late_tighter' else -change)
    return np.full(len(p['t']),base)
def eligibility(c,p):
    valid=p['age']>=c['grace'];w=c['window']
    if w=='before_last60':valid&=p['remaining']>=60
    elif w=='before_last120':valid&=p['remaining']>=120
    elif w=='last300':valid&=p['remaining']<=300
    elif w=='last120':valid&=p['remaining']<=120
    elif w=='first120':valid&=p['age']<=120
    elif w=='first300':valid&=p['age']<=300
    return valid
def firsts(r,p,cc):
    breach=np.array([eligibility(c,p)&(p['z']<thresholds(c,r,p)) for c in cc])
    ix=np.arange(len(p['t']),dtype=np.int16)[None,:]
    lastclear=np.maximum.accumulate(np.where(~breach,ix,-1),axis=1)
    elapsed=ix-lastclear-1
    qualified=breach&(elapsed>=np.array([c['persist'] for c in cc])[:,None])
    present=qualified.any(axis=1);first=qualified.argmax(axis=1);first[~present]=-1
    return first
def metric(rows,net,filled,fee,fr,mask,trigger=None):
    idx=np.flatnonzero(mask);hold=np.array([r['hold_net'] for r in rows])[idx];won=np.array([r['won'] for r in rows],dtype=bool)[idx]
    n=net[idx];delta=n-hold;f=filled[idx];e=fee[idx];ff=fr[idx]
    return dict(trades=len(idx),winners=int(won.sum()),losses=int((~won).sum()),hold=float(hold.sum()),net=float(n.sum()),delta=float(delta.sum()),loss_savings=float(delta[~won].sum()),losses_reduced=int((delta[~won]>1e-8).sum()),winners_cut=int((f&won).sum()),winner_cost=float(-delta[won].sum()),winners_turned_loss=int((won&(n<0)).sum()),exit_fees=float(e.sum()),friction=float(ff.sum()),fees_only_net=float(n.sum()+ff.sum()),triggered_winners=int(((trigger[idx]>=0)&won).sum()) if trigger is not None else None)
def selection(cc,rr,arrays):
    periods={'full':np.ones(len(rr),dtype=bool),'development':np.array([r['close']<SPLIT for r in rr]),'later':np.array([r['close']>=SPLIT for r in rr])}
    def measure(k,mask):return metric(rr,arrays['net_low'][k],arrays['fill_low'][k],arrays['fee_low'][k],arrays['friction_low'][k],mask,arrays['trigger'][k])
    metrics=[{p:measure(k,m) for p,m in periods.items()} for k in range(len(cc))]
    # Prefer simpler schedules and shorter parameter descriptions when gains tie.
    def rank(k,mask):
        m=measure(k,mask);c=cc[k];return (m['delta'],-m['winners_cut'],c['family'] in ['hold','absolute'],-c['persist'],-c['grace'],-k)
    def choose(mask,cut_limit):
        # Prior winner floors used the full cohort; keep as a control only.
        eligible=[k for k in range(len(cc)) if cc[k]['family']!='floors' and measure(k,mask)['triggered_winners']<=cut_limit]
        return max(eligible,key=lambda k:rank(k,mask))
    out={'common':{},'asset_specific':{},'hindsight':{},'periods':{p:dict(trades=int(m.sum()),wins=sum(bool(r['won']) for r,i in zip(rr,m) if i),losses=sum(not r['won'] for r,i in zip(rr,m) if i)) for p,m in periods.items()}}
    for frac in [0,.02,.05]:
        fit=periods['development'];budget=math.floor(sum(bool(r['won']) for r,i in zip(rr,fit) if i)*frac);k=choose(fit,budget)
        out['common'][str(frac)]=dict(index=k,config=cc[k],development_cut_budget=budget,metrics=metrics[k])
        selected={};pick=np.zeros(len(rr),dtype=int)
        for a in ASSETS:
            mask=np.array([r['asset']==a for r in rr]);train=fit&mask;n=int(train.sum());loss=sum(not r['won'] for r,i in zip(rr,train) if i)
            budget=math.floor(sum(bool(r['won']) for r,i in zip(rr,train) if i)*frac)
            ka=choose(train,budget) if n>=10 and loss>=2 else 0
            pick[mask]=ka;selected[a]=dict(index=ka,config=cc[ka],development_trades=n,development_losses=loss,reason='development selection' if n>=10 and loss>=2 else 'hold: insufficient development losses/trades')
        ii=np.arange(len(rr));mm={p:metric(rr,arrays['net_low'][pick,ii],arrays['fill_low'][pick,ii],arrays['fee_low'][pick,ii],arrays['friction_low'][pick,ii],m,arrays['trigger'][pick,ii]) for p,m in periods.items()}
        out['asset_specific'][str(frac)]=dict(choices=selected,metrics=mm)
    for budget in [0,4,8,16]:
        k=choose(periods['full'],budget);out['hindsight'][str(budget)]=dict(index=k,config=cc[k],metrics=metrics[k],label='Full-sample selected, exploratory only')
    return out,metrics
def main():
    d=pickle.load(gzip.open(PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(PREV/'seconds.pkl.gz','rb'));rr=d['actual'];cc=configs();save('configs.json',cc)
    K,N=len(cc),len(rr);print('configs',K,'trades',N,flush=True)
    arrays={}
    for mode in ['low','high']:
        arrays['net_'+mode]=np.empty((K,N));arrays['fill_'+mode]=np.zeros((K,N),dtype=bool);arrays['fee_'+mode]=np.zeros((K,N));arrays['friction_'+mode]=np.zeros((K,N))
    arrays['trigger']=np.full((K,N),-1,dtype=np.int64)
    for j,r in enumerate(rr):
        p=prepare(r,d,ss);first=firsts(r,p,cc);ok=first>=0;arrays['trigger'][ok,j]=p['t'][first[ok]]
        for mode in ['low','high']:
            arrays['net_'+mode][:,j]=r['hold_net']
            for dst,src in [('net','net'),('fill','fill'),('fee','fee'),('friction','friction')]:arrays[dst+'_'+mode][ok,j]=p[mode][src][first[ok]]
        if j%50==0:print('paths',j,'/',N,flush=True)
    np.savez_compressed(P/'grid_arrays.npz',**arrays)
    selected,metrics=selection(cc,rr,arrays);save('selection.json',selected);save('metrics.json',metrics)
    save('manifest.json',dict(configurations=K,trades=N,selection_cutoff='2026-10-12 00:00 ET',prior_history_already_inspected=True,input_hashes={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [PREV/'inputs.pkl.gz',PREV/'seconds.pkl.gz']}))
    for kind in ['common','asset_specific','hindsight']:
        for key,v in selected[kind].items():print(kind,key,json.dumps({k:v[k] for k in ['config','metrics'] if k in v}),flush=True)
if __name__=='__main__':main()
