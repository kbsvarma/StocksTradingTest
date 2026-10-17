"""Predict policy-specific proceeds, rather than just the settlement winner."""
import collections,datetime as dt,gzip,json,sys
import numpy as np
import study as s
from engine import cash,entry,exit_trade,screen
P=s.P;TIMES=(14,12,10,8,6,4,2)
NAMES=('hold','loss_0.05','loss_0.1','loss_0.15','loss_0.2','salvage_0.1','salvage_0.15','salvage_0.2','salvage_0.25','late3_2_0.1','late3_2_0.15','late3_2_0.2','confirmed_2_0.1','confirmed_2_0.15')
def features(x,j,side):
    row=x[j];p=(row[0]+row[1])/2
    if side==-1:p=1-p
    z,m1,m3,vol=row[6],row[7],row[10],row[9];missing=int(not np.isfinite(z+m1+m3+vol))
    z=0 if not np.isfinite(z) else np.clip(side*z,-6,6);m1=0 if not np.isfinite(m1) else np.clip(side*m1,-6,6);m3=0 if not np.isfinite(m3) else np.clip(side*m3,-6,6)
    prev=(x[j-1,0]+x[j-1,1])/2 if j else np.nan;delta=0 if not np.isfinite(prev) else side*((row[0]+row[1])/2-prev)
    lr=np.log((14-j)/7);lv=np.log(vol) if np.isfinite(vol) and vol>0 else np.log(.001)
    return np.array([1.,p-.5,p*p,z,m1,m3,lr,(p-.5)*lr,delta,lv,missing])
def fit():
    x,mm=s.load();_,all_ex=s.configurations();exits=[next(e for e in all_ex if e['name']==name) for name in NAMES];models={}
    for ex in exits:
        xx=[];yy=[];mi=[]
        for i,m in enumerate(mm):
            if s.co(m['close'])!=0:continue
            for rem in TIMES:
                j=14-rem
                for side,pol in ((1,2),(-1,3)):
                    ent=entry(x[i],pol,rem,0)
                    if ent[0]!=j:continue
                    f=features(x[i],j,side)
                    if not np.isfinite(f).all():continue
                    xp,k,sold,armed,trig=exit_trade(x[i],m['result'],ent,s.xarray(ex));proceeds=float(cash(xp,False))-.0025 if sold else xp
                    mid=(x[i,j,0]+x[i,j,1])/2;mid=mid if side==1 else 1-mid
                    xx.append(f);yy.append(proceeds-mid);mi.append(i)
        raw=np.array(xx);y=np.array(yy);_,ix,ns=np.unique(mi,return_inverse=True,return_counts=True);w=1/ns[ix]
        mean=np.average(raw,axis=0,weights=w);scale=np.sqrt(np.average((raw-mean)**2,axis=0,weights=w));mean[0]=0;scale[0]=1;scale[scale<1e-9]=1;X=(raw-mean)/scale
        beta=np.linalg.solve((X.T*w)@X+10*np.eye(X.shape[1]),X.T@(w*y))
        models[ex['name']]=dict(exit=ex,coef=beta.tolist(),mean=mean.tolist(),scale=scale.tolist(),rows=len(y),markets=len(ns))
    s.save('path_models.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.sha(P/'PASS2.md'),models=models));print('Fitted path models',len(models),flush=True)
def transform(x,model):
    out=x.copy();valid=np.isfinite(x[:,:,0]+x[:,:,1]);coef=np.array(model['coef']);mean=np.array(model['mean']);scale=np.array(model['scale'])
    # These features contain no final label or future price.
    for side in (1,-1):
        raw=batch_features(x,side);pred=((raw-mean)/scale@coef).reshape(x.shape[:2]);mid=(x[:,:,0]+x[:,:,1])/2
        if side==1:yes=mid+pred
        else:no=1-mid+pred
    out[:,:,12]=np.where(valid,np.clip((1+yes-no)/2,0,1),np.nan)
    return out
def batch_features(x,side):
    mid=(x[:,:,0]+x[:,:,1])/2;p=mid if side==1 else 1-mid;z,m1,m3,vol=(x[:,:,i] for i in (6,7,10,9));missing=(~np.isfinite(z+m1+m3+vol)).astype(float)
    clean=lambda v:np.where(np.isfinite(v),np.clip(side*v,-6,6),0)
    prev=np.roll(mid,1,axis=1);prev[:,0]=np.nan;delta=np.where(np.isfinite(prev),side*(mid-prev),0);lr=np.broadcast_to(np.log(np.arange(14,0,-1)/7),mid.shape);lv=np.log(np.where(np.isfinite(vol)&(vol>0),vol,.001))
    return np.stack([np.ones(mid.shape),p-.5,p*p,clean(z),clean(m1),clean(m3),lr,(p-.5)*lr,delta,lv,missing],axis=-1).reshape(-1,11)
def grid(x,mm,model,max_co):
    z=transform(x,model);ent=np.array([[12,rem,0] for rem in TIMES],float);xx=np.array([s.xarray(model['exit'])]);co=np.array([s.co(m['close']) for m in mm]);y=np.array([m['result'] for m in mm])
    return screen(z,y,co,ent,xx,0,max_co),z
def select():
    x,mm=s.load();spec=json.loads((P/'path_models.json').read_text());assert spec['plan_sha256']==s.sha(P/'PASS2.md');configs=[];stats=[]
    for name,model in spec['models'].items():
        arr,z=grid(x,mm,model,1)
        for i,rem in enumerate(TIMES):
            c=dict(id=len(configs),entry=dict(id=0,policy=12,policy_name='path_adjusted_'+name,rem=rem,band=0),exit=model['exit'],path_model=name);configs.append(c);stats.append(arr[i])
    arr=np.array(stats);den=collections.Counter(s.co(m['close']) for m in mm);eligible=[c for c in configs if arr[c['id'],1,0]>=.98*den[1]]
    ranked=sorted(eligible,key=lambda c:arr[c['id'],1,1],reverse=True);chosen=ranked[:2]
    s.save('path_configs.json',configs);np.save(P/'path_early_screen.npy',arr);s.save('path_frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),models_sha256=s.sha(P/'path_models.json'),selected_ids=[c['id'] for c in chosen],trials=len(configs),early_positive=int(sum(arr[c['id'],1,1]>0 for c in eligible)),leaders=[dict(config=c,early=arr[c['id'],:2].tolist()) for c in ranked[:8]]))
    print('PATH FROZEN',[(c['id'],c['path_model'],c['entry']['rem'],arr[c['id'],:2,1].tolist()) for c in ranked[:8]],flush=True)
def evaluate():
    x,mm=s.load();models=json.loads((P/'path_models.json').read_text())['models'];cc=json.loads((P/'path_configs.json').read_text());f=json.loads((P/'path_frozen_selection.json').read_text());assert f['models_sha256']==s.sha(P/'path_models.json');all_stats=[];selected=set(f['selected_ids']);selected.add(0);out={}
    for name,model in models.items():
        arr,z=grid(x,mm,model,4);all_stats.extend(arr)
        for c in [c for c in cc if c['path_model']==name and c['id'] in selected]:
            rr=s.rows(c,z,mm);dd=s.rows(c,z,mm,1)
            with gzip.open(P/f'path_trades_{c["id"]}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
            pp={}
            for ci,co in enumerate(s.PERIODS):
                den=sum(s.co(m['close'])==ci for m in mm);r=[v for v in rr if v['cohort']==co];d=[v for v in dd if v['cohort']==co];pp[co]=dict(base=s.metrics(r,den,ci),cost1c=s.metrics(r,den,ci,.01),cost2c=s.metrics(r,den,ci,.02),delay60=s.metrics(d,den,ci))
            out[c['id']]=dict(config=c,periods=pp);print('PATH',c['id'],name,c['entry']['rem'],[(co,pp[co]['base']['net']) for co in s.PERIODS],flush=True)
    np.save(P/'path_all_periods.npy',np.array(all_stats));s.save('path_results.json',out)
if __name__=='__main__':{'fit':fit,'select':select,'evaluate':evaluate}[sys.argv[1]]()
