import collections,datetime as dt,gzip,itertools,json,sys
import numpy as np
from numba import njit
import study as s
from engine import cash,entry,exit_trade
P=s.P
def configs():
    _,all_ex=s.configurations();exits=[]
    for mode,amount,confirm in itertools.product((1,2),(.05,.10,.15,.20),(1,2)):
        if mode==2 and amount==.05:continue
        exits.append(dict(id=0,name=f'{mode}_{amount}_{confirm}',family='recovery',mode=mode,amount=amount,activate=14,confirm=confirm,arm=9,model_col=8,margin=0))
    out=[]
    for policy,rem,ex,trigger,momentum,second_stop in itertools.product((0,9,12),(14,12,10,8,6),exits,(-1.,0.,.02,.05),(0,1),(0,1)):
        out.append(dict(id=len(out),entry=dict(policy=policy,rem=rem,band=0,policy_name=s.POLICIES[policy]),exit=ex,recovery_trigger=trigger,momentum=momentum,second_stop=second_stop))
    return out
@njit(cache=True)
def sim(x,result,ec,ex,trigger,momentum,second_stop):
    ent=entry(x,int(ec[0]),int(ec[1]),0)
    if ent[0]<0:return np.zeros(16)
    j,side,price,fallback=ent;xp,k,sold,armed,trig=exit_trade(x,result,ent,ex);debit=cash(price,True);credit=cash(xp,False) if sold else xp;first=debit+credit-.0025*(1+int(sold));hold=debit+int(result==side)-.0025
    # n, first P&L, total P&L, additional P&L, entries, transactions, won-first, second entry/exit details.
    out=np.array([1.,first,first,0.,1.,1.+int(sold),float(int(result==side)),float(j),float(side),price,xp,float(k),-1.,0.,0.,0.])
    if not sold:return out
    for jj in range(k+1,13):
        b,a=x[jj,0],x[jj,1]
        if np.isnan(b+a) or not 0<=b<=a<=1 or a-b>.05000001:continue
        side2=-side if trigger<0 else side;bid=b if side==1 else 1-a
        if trigger>=0 and bid<price+trigger-1e-8:continue
        if momentum and (np.isnan(x[jj,7]) or side2*x[jj,7]<.25):continue
        eb,ea=x[jj,2],x[jj,3]
        if np.isnan(eb+ea) or not 0<=eb<=ea<=1 or ea-eb>.05000001:continue
        ep=ea if side2==1 else 1-eb
        if not 0<ep<1:continue
        e2=ex.copy()
        if not second_stop:e2[0]=0
        op,kk,ss,aa,tt=exit_trade(x,result,(jj,side2,ep,0),e2);extra=cash(ep,True)+(cash(op,False) if ss else op)-.0025*(1+int(ss))
        out[2]+=extra;out[3]=extra;out[4]=2;out[5]+=1+int(ss);out[12]=jj;out[13]=ep;out[14]=op;out[15]=kk
        break
    return out
@njit(cache=True)
def screen(x,y,co,ecs,exs,rr,max_co=1):
    a=np.zeros((len(rr),5,10))
    for ci in range(len(rr)):
        for i in range(len(x)):
            period=int(co[i])
            if period>max_co:continue
            r=sim(x[i],y[i],ecs[ci],exs[ci],rr[ci,0],int(rr[ci,1]),int(rr[ci,2]))
            if not r[0]:continue
            o=a[ci,period];o[0]+=1;o[1]+=r[2];o[2]+=int(r[2]>0);o[3]+=r[1];o[4]+=r[3];o[5]+=r[4];o[6]+=r[5];o[7]+=r[6];o[8]+=r[2] if r[2]>0 else 0;o[9]+=r[2] if r[2]<0 else 0
    return a
def run_arrays(cc,max_co):
    x,mm=s.load();return screen(x,np.array([m['result'] for m in mm]),np.array([s.co(m['close']) for m in mm]),np.array([s.earray(c['entry']) for c in cc]),np.array([s.xarray(c['exit']) for c in cc]),np.array([[c[k] for k in ('recovery_trigger','momentum','second_stop')] for c in cc]),max_co)
def select():
    cc=configs();a=run_arrays(cc,1);_,mm=s.load();den=collections.Counter(s.co(m['close']) for m in mm);selected=[]
    for flip,mom in itertools.product((False,True),(0,1)):
        candidates=[c for c in cc if (c['recovery_trigger']<0)==flip and c['momentum']==mom and all(a[c['id'],i,0]>=.98*den[i] for i in (0,1))]
        selected.append(max(candidates,key=lambda c:min(a[c['id'],i,1]/a[c['id'],i,0] for i in (0,1))))
    s.save('recovery_configs.json',cc);np.save(P/'recovery_early.npy',a);s.save('recovery_frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=s.sha(P/'PASS4.md'),trials=len(cc),selected_ids=[c['id'] for c in selected],leaders=[dict(config=c,early=a[c['id'],:2].tolist()) for c in selected]));print('RECOVERY FROZEN',[(c['id'],c['entry'],c['exit']['name'],c['recovery_trigger'],c['momentum'],c['second_stop'],a[c['id'],:2,1].tolist()) for c in selected],flush=True)
def evaluate():
    cc=json.loads((P/'recovery_configs.json').read_text());f=json.loads((P/'recovery_frozen_selection.json').read_text());assert f['plan_sha256']==s.sha(P/'PASS4.md');a=run_arrays(cc,4);np.save(P/'recovery_all_periods.npy',a);x,mm=s.load();out={}
    for cid in f['selected_ids']:
        c=cc[cid];rr=[]
        for i,m in enumerate(mm):
            r=sim(x[i],m['result'],s.earray(c['entry']),s.xarray(c['exit']),c['recovery_trigger'],c['momentum'],c['second_stop'])
            if r[0]:rr.append(dict(ticker=m['ticker'],close=m['close'],cohort=s.PERIODS[s.co(m['close'])],values=r.tolist()))
        with gzip.open(P/f'recovery_trades_{cid}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
        pp={}
        for ci,co in enumerate(s.PERIODS):
            rows=[r for r in rr if r['cohort']==co];z=np.array([r['values'] for r in rows]);pnl=z[:,2];eq=np.r_[0,np.cumsum(pnl)];daily=collections.defaultdict(float)
            for r,p in zip(rows,pnl):daily[dt.datetime.fromtimestamp(r['close'],dt.timezone.utc).date().isoformat()]+=float(p)
            pp[co]=dict(windows=len(rows),net=float(pnl.sum()),first_leg_net=float(z[:,1].sum()),extra_leg_net=float(z[:,3].sum()),entry_orders=int(z[:,4].sum()),transactions=int(z[:,5].sum()),cash_win_rate=float((pnl>0).mean()),original_win_rate=float(z[:,6].mean()),mean_win=float(pnl[pnl>0].mean()) if (pnl>0).any() else None,mean_loss=float(pnl[pnl<0].mean()) if (pnl<0).any() else None,drawdown=float(np.max(np.maximum.accumulate(eq)-eq)),cost1c=float((pnl-.0075*z[:,5]).sum()),cost2c=float((pnl-.0175*z[:,5]).sum()),daily=dict(daily))
        out[cid]=dict(config=c,periods=pp);print('RECOVERY',cid,[(co,pp[co]['net'],pp[co]['extra_leg_net']) for co in s.PERIODS],flush=True)
    s.save('recovery_results.json',out)
if __name__=='__main__':{'select':select,'evaluate':evaluate}[sys.argv[1]]()
