import collections,datetime as dt,gzip,itertools,json,math,sys
import numpy as np
from numba import njit
import study as s
from engine import cash,entry
P=s.P;TIMES=(14,12,10,8,6);TRIGGERS=((1,.05),(1,.1),(1,.15),(1,.2),(2,.1),(2,.15),(2,.2))
@njit(cache=True)
def feature(x,j,k,side,price,peak,trough):
    b,a=x[k,0],x[k,1];mid=(b+a)/2 if side==1 else 1-(b+a)/2;mid=min(.999,max(.001,mid));v=np.zeros(12)
    v[0]=1;v[1]=math.log(mid/(1-mid));v[2]=0 if np.isnan(x[k,6]) else max(-6,min(6,side*x[k,6]));v[3]=0 if np.isnan(x[k,7]) else max(-6,min(6,side*x[k,7]));v[4]=0 if np.isnan(x[k,10]) else max(-6,min(6,side*x[k,10]));v[5]=price;bid=b if side==1 else 1-a;v[6]=bid-price;v[7]=peak-price;v[8]=trough-price;v[9]=math.log((14-k)/7);v[10]=(k-j)/(14-j);v[11]=side*((b+a)/2-(x[k-1,0]+x[k-1,1])/2) if k>0 and np.isfinite(x[k-1,0]+x[k-1,1]) else 0
    return v
@njit(cache=True)
def hits(mode,amount,bid,price):return bid<=price-amount+1e-8 if mode==1 else price>amount and bid<=amount+1e-8
def fit():
    x,mm=s.load();models={}
    for mode,amount in TRIGGERS:
        X=[];Y=[];M=[]
        for i,m in enumerate(mm):
            if s.co(m['close'])!=0:continue
            for rem,policy in itertools.product(TIMES,(0,12)):
                ent=entry(x[i],policy,rem,0);j,side,price,_=ent
                if j<0:continue
                peak=price;trough=price
                for k in range(j+1,14):
                    b,a=x[i,k,:2]
                    if not np.isfinite(b+a):continue
                    bid=b if side==1 else 1-a;peak=max(peak,bid);trough=min(trough,bid)
                    if hits(mode,amount,bid,price):X.append(feature(x[i],j,k,side,price,peak,trough));Y.append(int(side==m['result']));M.append(i)
        raw=np.array(X);y=np.array(Y);_,ix,n=np.unique(M,return_inverse=True,return_counts=True);w=1/n[ix];mean=np.average(raw,axis=0,weights=w);scale=np.sqrt(np.average((raw-mean)**2,axis=0,weights=w));mean[:2]=0;scale[:2]=1;scale[scale<1e-9]=1;xx=(raw-mean)/scale;beta=np.zeros(12);beta[1]=1;prior=beta.copy()
        for _ in range(100):
            p=1/(1+np.exp(-np.clip(xx@beta,-30,30)));g=xx.T@(w*(p-y))+10*(beta-prior);h=(xx.T*(w*p*(1-p)))@xx+10*np.eye(12);step=np.linalg.solve(h,g);beta-=step
            if np.max(abs(step))<1e-9:break
        models[f'{mode}_{amount}']=dict(mode=mode,amount=amount,mean=mean.tolist(),scale=scale.tolist(),coef=beta.tolist(),training_rows=len(y),training_markets=len(n))
    s.save('conditional_models.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.sha(P/'PASS5.md'),models=models));print('Conditional models fitted',len(models),flush=True)
@njit(cache=True)
def sim(x,result,policy,rem,mode,amount,buffer,mean,scale,beta):
    ent=entry(x,policy,rem,0);j,side,price,_=ent
    if j<0:return np.zeros(14)
    won=int(side==result);xp=1. if won else 0.;kout=14;sold=False;peak=price;trough=price;trigger=-1
    for k in range(j+1,14):
        b,a=x[k,0],x[k,1]
        if not np.isfinite(b+a) and trigger<0:continue
        bid=b if side==1 else 1-a
        if np.isfinite(bid):peak=max(peak,bid);trough=min(trough,bid)
        if trigger<0 and hits(mode,amount,bid,price):
            f=feature(x,j,k,side,price,peak,trough);p=1/(1+math.exp(-max(-30,min(30,np.sum((f-mean)/scale*beta)))))
            if p<=cash(bid,False)-.0025+buffer:trigger=k
        if trigger>=0:
            eb,ea=x[k,2],x[k,3]
            if not np.isfinite(eb+ea):continue
            val=eb if side==1 else 1-ea
            if 0<val<1:xp=val;kout=k;sold=True;break
    debit=cash(price,True);credit=cash(xp,False) if sold else xp;net=debit+credit-.0025*(1+int(sold));hold=debit+won-.0025
    return np.array([1.,net,float(int(net>0)),float(won),float(int(sold)),hold,credit-.0025 if sold and not won else 0.,1-credit+.0025 if sold and won else 0.,float(j),float(side),price,xp,float(kout),float(trigger)])
@njit(cache=True)
def screen(x,y,co,pol,rem,mode,amount,buffer,mean,scale,beta,max_co):
    out=np.zeros((5,10))
    for i in range(len(x)):
        ci=int(co[i])
        if ci>max_co:continue
        r=sim(x[i],y[i],pol,rem,mode,amount,buffer,mean,scale,beta)
        if not r[0]:continue
        out[ci,0]+=1;out[ci,1]+=r[1];out[ci,2]+=r[2];out[ci,3]+=r[3];out[ci,4]+=r[4];out[ci,5]+=r[5];out[ci,6]+=r[6];out[ci,7]+=r[7];out[ci,8]+=r[1] if r[1]>0 else 0;out[ci,9]+=r[1] if r[1]<0 else 0
    return out
def configs():
    models=json.loads((P/'conditional_models.json').read_text())['models'];out=[]
    for key in models:
        for rem,pol,buffer in itertools.product(TIMES,(0,12),(0.,.02,.05)):out.append(dict(id=len(out),model=key,rem=rem,policy=pol,buffer=buffer))
    return out,models
def compute(cc,models,max_co):
    x,mm=s.load();y=np.array([m['result'] for m in mm]);co=np.array([s.co(m['close']) for m in mm]);out=[]
    for c in cc:
        m=models[c['model']];out.append(screen(x,y,co,c['policy'],c['rem'],m['mode'],m['amount'],c['buffer'],np.array(m['mean']),np.array(m['scale']),np.array(m['coef']),max_co))
    return np.array(out)
def select():
    cc,models=configs();a=compute(cc,models,1);_,mm=s.load();den=collections.Counter(s.co(m['close']) for m in mm);eligible=[c for c in cc if all(a[c['id'],i,0]>=.98*den[i] for i in (0,1))];ranked=sorted(eligible,key=lambda c:min(a[c['id'],i,1]/a[c['id'],i,0] for i in (0,1)),reverse=True);chosen=[]
    for c in ranked:
        if c['model'] in [v['model'] for v in chosen]:continue
        chosen.append(c)
        if len(chosen)==2:break
    s.save('conditional_configs.json',cc);np.save(P/'conditional_early.npy',a);s.save('conditional_frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),models_sha256=s.sha(P/'conditional_models.json'),selected_ids=[c['id'] for c in chosen],trials=len(cc),leaders=[dict(config=c,early=a[c['id'],:2].tolist()) for c in chosen]));print('CONDITIONAL FROZEN',[(c,a[c['id'],:2,1].tolist()) for c in chosen],flush=True)
def evaluate():
    cc,models=configs();f=json.loads((P/'conditional_frozen_selection.json').read_text());assert f['models_sha256']==s.sha(P/'conditional_models.json');a=compute(cc,models,4);np.save(P/'conditional_all_periods.npy',a);x,mm=s.load();out={}
    for cid in f['selected_ids']:
        c=cc[cid];model=models[c['model']];rr=[]
        for i,m in enumerate(mm):
            r=sim(x[i],m['result'],c['policy'],c['rem'],model['mode'],model['amount'],c['buffer'],np.array(model['mean']),np.array(model['scale']),np.array(model['coef']))
            if not r[0]:continue
            _,net,pos,won,sold,hold,saved,forfeit,j,side,price,xp,k,trig=r;j=int(j);k=int(k);sold=bool(sold);T=m['start']+60*(j+1);xt=m['start']+60*(k+1)+3 if sold else max(m['close']+1,m['settled']);debit=float(cash(price,True));credit=float(cash(xp,False)) if sold else xp
            rr.append(dict(ticker=m['ticker'],cohort=s.PERIODS[s.co(m['close'])],close=m['close'],entered=T+3,decision=T,entry_index=j,side=int(side),entry=price,exit=xp,exit_ts=xt,sold=sold,trigger_index=int(trig),armed=False,forecast_fallback=False,debit=debit,credit=credit,legs=1+int(sold),settlement_win=int(won),pnl=debit+credit,hold_pnl=debit+won))
        with gzip.open(P/f'conditional_trades_{cid}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
        pp={}
        for ci,co in enumerate(s.PERIODS):
            r=[v for v in rr if v['cohort']==co];den=sum(s.co(m['close'])==ci for m in mm);pp[co]=dict(base=s.metrics(r,den,ci),cost1c=s.metrics(r,den,ci,.01),cost2c=s.metrics(r,den,ci,.02))
        out[cid]=dict(config=c,periods=pp);print('CONDITIONAL',cid,[(co,pp[co]['base']['net'],pp[co]['base']['exit_improvement']) for co in s.PERIODS],flush=True)
    s.save('conditional_results.json',out)
if __name__=='__main__':{'fit':fit,'select':select,'evaluate':evaluate}[sys.argv[1]]()
