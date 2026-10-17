"""Stop-payoff-aware entry search with unconditional entry deadline."""
import collections,datetime as dt,gzip,json,sys
import numpy as np
from numba import njit
import study as s,path_model as pm
from engine import cash,direction,exit_trade
P=s.P;THRESHOLDS=(0.,.01,.02);DEADLINES=(8,4,2)
def predict(x,model):
    coef=np.array(model['coef']);mean=np.array(model['mean']);scale=np.array(model['scale']);mid=(x[:,:,0]+x[:,:,1])/2
    yes=mid+((pm.batch_features(x,1)-mean)/scale@coef).reshape(mid.shape);no=1-mid+((pm.batch_features(x,-1)-mean)/scale@coef).reshape(mid.shape)
    out=x.copy();out[:,:,12]=np.clip((1+yes-no)/2,0,1)
    def debit(p):
        fee=np.ceil(.07*p*(1-p)*1e6-1e-8)/1e6
        return np.ceil((p+fee)/.0001-1e-8)*.0001
    score=np.maximum(yes-debit(x[:,:,1])-.0025,no-debit(1-x[:,:,0])-.0025)
    return out,score
@njit(cache=True)
def dynamic_entry(x,score,threshold,deadline,delay=0):
    for j in range(14-delay):
        b,a=x[j,0],x[j,1]
        if np.isnan(b+a) or not 0<=b<=a<=1 or a-b>.05000001:continue
        if 14-j>deadline and (np.isnan(score[j]) or score[j]<threshold):continue
        side,fallback=direction(x[j],12);off=4 if delay else 2;eb,ea=x[j,off],x[j,off+1]
        if np.isnan(eb+ea) or not 0<=eb<=ea<=1 or ea-eb>.05000001:continue
        price=ea if side==1 else 1-eb
        if not 0<price<1:continue
        return j,side,price,fallback
    return -1,0,0.,0
@njit(cache=True)
def screen(x,score,y,co,ex,max_co=1):
    out=np.zeros((9,5,14))
    for ti,threshold in enumerate((0.,.01,.02)):
        for di,deadline in enumerate((8,4,2)):
            cid=ti*3+di
            for i in range(len(x)):
                cc=int(co[i])
                if cc>max_co:continue
                ent=dynamic_entry(x[i],score[i],threshold,deadline)
                if ent[0]<0:continue
                debit=cash(ent[2],True);won=int(y[i]==ent[1]);hold=debit+won-.0025;price,k,sold,armed,trigger=exit_trade(x[i],y[i],ent,ex);proceeds=cash(price,False) if sold else price;net=debit+proceeds-.0025*(1+int(sold));o=out[cid,cc]
                o[0]+=1;o[1]+=net;o[2]+=net*net;o[3]+=int(net>1e-10);o[4]+=int(sold);o[5]+=won;o[6]+=hold
                if sold and won==0:o[7]+=proceeds-.0025;o[10]+=1
                if sold and won==1:o[8]+=1-proceeds+.0025;o[9]+=1
                o[11]+=ent[2];o[12]+=net if net>0 else 0;o[13]+=net if net<0 else 0
    return out
def select():
    x,mm=s.load();models=json.loads((P/'path_models.json').read_text())['models'];co=np.array([s.co(m['close']) for m in mm]);y=np.array([m['result'] for m in mm]);configs=[];stats=[];den=collections.Counter(co)
    for name,model in models.items():
        z,score=predict(x,model);a=screen(z,score,y,co,s.xarray(model['exit']),1);stats.extend(a)
        for threshold in THRESHOLDS:
            for deadline in DEADLINES:configs.append(dict(id=len(configs),path_model=name,exit=model['exit'],threshold=threshold,deadline=deadline))
    a=np.array(stats);eligible=[c for c in configs if all(a[c['id'],i,0]>=.98*den[i] for i in (0,1))];ranked=sorted(eligible,key=lambda c:min(a[c['id'],i,1]/a[c['id'],i,0] for i in (0,1)),reverse=True);chosen=[]
    for c in ranked:
        if c['exit']['family'] in [v['exit']['family'] for v in chosen]:continue
        chosen.append(c)
        if len(chosen)==2:break
    np.save(P/'deadline_early.npy',a);s.save('deadline_configs.json',configs);s.save('deadline_frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.sha(P/'PASS3.md'),models_sha256=s.sha(P/'path_models.json'),selected_ids=[c['id'] for c in chosen],trials=len(configs),leaders=[dict(config=c,early=a[c['id'],:2].tolist()) for c in ranked[:8]]));print('DEADLINE FROZEN',[(c['id'],c['path_model'],c['threshold'],c['deadline'],a[c['id'],:2,1].tolist()) for c in chosen],flush=True)
def rows(c,z,score,mm,delay=0):
    rr=[]
    for i,m in enumerate(mm):
        ent=dynamic_entry(z[i],score[i],c['threshold'],c['deadline'],delay)
        if ent[0]<0:continue
        j,side,price,fallback=ent;xp,k,sold,armed,trig=exit_trade(z[i],m['result'],ent,s.xarray(c['exit']),delay);T=m['start']+60*(j+1);xt=m['start']+60*(k+1)+(60 if delay else 3) if sold else max(m['close']+1,m['settled']);debit=float(cash(price,True));credit=float(cash(xp,False)) if sold else xp;won=int(m['result']==side)
        rr.append(dict(ticker=m['ticker'],cohort=s.PERIODS[s.co(m['close'])],close=m['close'],entered=T+(60 if delay else 3),decision=T,entry_index=int(j),side=int(side),entry=float(price),exit=float(xp),exit_ts=int(xt),sold=bool(sold),trigger_index=int(trig),armed=bool(armed),forecast_fallback=bool(fallback),debit=debit,credit=credit,legs=1+int(sold),settlement_win=won,pnl=debit+credit,hold_pnl=debit+won,deadline_entry=bool(14-j<=c['deadline'])))
    return rr
def evaluate():
    x,mm=s.load();models=json.loads((P/'path_models.json').read_text())['models'];co=np.array([s.co(m['close']) for m in mm]);y=np.array([m['result'] for m in mm]);cc=json.loads((P/'deadline_configs.json').read_text());f=json.loads((P/'deadline_frozen_selection.json').read_text());assert f['plan_sha256']==s.sha(P/'PASS3.md') and f['models_sha256']==s.sha(P/'path_models.json');stats=[];out={}
    for name,model in models.items():
        z,score=predict(x,model);stats.extend(screen(z,score,y,co,s.xarray(model['exit']),4))
        for c in [c for c in cc if c['path_model']==name and c['id'] in f['selected_ids']]:
            rr=rows(c,z,score,mm);dd=rows(c,z,score,mm,1)
            with gzip.open(P/f'deadline_trades_{c["id"]}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
            pp={}
            for ci,cohort in enumerate(s.PERIODS):
                den=sum(s.co(m['close'])==ci for m in mm);r=[v for v in rr if v['cohort']==cohort];d=[v for v in dd if v['cohort']==cohort];pp[cohort]=dict(base=s.metrics(r,den,ci),cost1c=s.metrics(r,den,ci,.01),cost2c=s.metrics(r,den,ci,.02),delay60=s.metrics(d,den,ci))
            out[c['id']]=dict(config=c,periods=pp);print('DEADLINE',c['id'],c['path_model'],c['threshold'],c['deadline'],[(co,pp[co]['base']['net']) for co in s.PERIODS],flush=True)
    np.save(P/'deadline_all_periods.npy',np.array(stats));s.save('deadline_results.json',out)
if __name__=='__main__':{'select':select,'evaluate':evaluate}[sys.argv[1]]()
