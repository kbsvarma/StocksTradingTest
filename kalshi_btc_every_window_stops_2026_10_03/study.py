from pathlib import Path
import collections,datetime as dt,gzip,hashlib,itertools,json,pickle,sys
import numpy as np
from engine import cash,direction,entry,exit_trade,screen
P=Path(__file__).resolve().parent;R=P.parent
PERIODS=('development','validation','later','october2','october3')
DATES=('2026-08-13','2026-08-28','2026-09-12','2026-10-02','2026-10-03','2026-10-04')
BOUNDS=[int(dt.datetime.fromisoformat(d+'T00:00:00+00:00').timestamp()) for d in DATES]
POLICIES=('favorite','underdog','always_yes','always_no','spot_direction','spot_reversal','momentum1','momentum3','momentum3_reversal','rich_value','official_direction','physical_value','blended_value')
def co(t):return int(np.searchsorted(BOUNDS[1:],t,side='right'))
def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
    src=R/'kalshi_protected_profit_revisit_2026_10_03';xx=[];mm=[];sources=[]
    for arr,meta in [('market_arrays.npy','markets.json'),('oct3_arrays.npy','oct3_markets.json')]:
        x=np.load(src/arr);m=json.loads((src/meta).read_text());idx=[i for i,r in enumerate(m) if r['asset']=='BTC'];xx.append(x[idx]);mm.extend(m[i] for i in idx)
        sources.extend(dict(path=str(src/n),sha256=sha(src/n)) for n in (arr,meta))
    base=np.concatenate(xx);x=np.full((*base.shape[:2],14),np.nan);x[:,:,:10]=base
    old=R/'kalshi_every_window_2026_10_03/prepared.pickle';sources.append(dict(path=str(old),sha256=sha(old)));look={m['ticker']:i for i,m in enumerate(mm)}
    for m in pickle.load(old.open('rb')):
        if m['asset']!='BTC':continue
        i=look[m['ticker']]
        for rem,f in m['new_features'].items():
            if 'xrich' in f:x[i,14-rem,10]=f['xrich'][7]
    spot=R/'kalshi_path_designs_2026_10_03/oct3_public/spot_BTC.jsonl';bb={}
    for row in map(json.loads,spot.read_text().splitlines()):
        for r in row['rows']:bb[int(r[0])]=float(r[4])
    for i,m in enumerate(mm):
        if co(m['close'])!=4:continue
        for j in range(14):
            t=m['start']+60*(j+1)
            if t-60 in bb and t-240 in bb and x[i,j,9]>0:x[i,j,10]=np.log(bb[t-60]/bb[t-240])/(x[i,j,9]*np.sqrt(3))
    probs=R/'kalshi_probability_model_2026_10_03';z=np.load(probs/'inputs/rows.npy');mmeta=json.loads((probs/'inputs/markets.json').read_text());pred=np.load(probs/'forecasts.npz')
    for row,pemp,pblend in zip(z,pred['empirical'],pred['blended']):
        mi,t,rem=row[:3];m=mmeta[int(mi)]
        if m['asset']!='BTC' or m['ticker'] not in look:continue
        i=look[m['ticker']];j=14-int(rem);x[i,j,11:14]=pemp,pblend,-row[5]
    for n in ('inputs/rows.npy','inputs/markets.json','forecasts.npz','models.json','distributions.npz'):sources.append(dict(path=str(probs/n),sha256=sha(probs/n)))
    np.save(P/'inputs/x.npy',x);(P/'inputs/markets.json').write_text(json.dumps(mm,separators=(',',':')))
    save('input_manifest.json',dict(sources=sources,columns=['signal_yes_bid','signal_yes_ask','next_open_yes_bid','next_open_yes_ask','next_close_yes_bid','next_close_yes_ask','coinbase_z_yes','momentum1','rich_p_yes','coinbase_sigma60','momentum3','physical_p_yes','blended_p_yes','official_signed_distance'],counts=dict(collections.Counter(PERIODS[co(m['close'])] for m in mm)),note='Reused frozen predictions, official and prior rich models fitted on early data only; no re-estimation in this experiment.'))
    print('Prepared BTC markets',len(mm),flush=True)
def load():return np.load(P/'inputs/x.npy'),json.loads((P/'inputs/markets.json').read_text())
def configurations():
    entries=[]
    for policy in range(len(POLICIES)):
        for rem,band in [(r,0) for r in (14,12,10,8,6,4,2,1)]+[(8,1),(4,1)]:entries.append(dict(id=len(entries),policy=policy,policy_name=POLICIES[policy],rem=rem,band=band))
    exits=[]
    def add(name,family,mode,amount=0,activate=14,confirm=1,arm=9,col=8,margin=0):
        exits.append(dict(id=len(exits),name=name,family=family,mode=mode,amount=amount,activate=activate,confirm=confirm,arm=arm,model_col=col,margin=margin))
    add('hold','hold',0)
    for amount in (.03,.05,.08,.10,.15,.20,.25,.30):add(f'loss_{amount}','tight',1,amount)
    for amount in (.05,.10,.15,.20,.25,.30,.40,.45,.50):add(f'salvage_{amount}','salvage',2,amount)
    for mode,amount in [(1,a) for a in (.05,.10,.15,.20)]+[(2,a) for a in (.10,.15,.20,.30,.40)]:
        add(f'confirmed_{mode}_{amount}','confirmed',mode,amount,confirm=2)
        if amount in (.10,.15):add(f'confirmed3_{mode}_{amount}','confirmed',mode,amount,confirm=3)
    for activate in (8,5,3,2,1):
        for mode,amount in [(1,a) for a in (.05,.10,.15,.20)]+[(2,a) for a in (.10,.15,.20,.30)]:add(f'late{activate}_{mode}_{amount}','late_salvage' if mode==2 else 'late_tight',mode,amount,activate=activate)
    for amount in (.10,.15,.20):add(f'time_scaled_{amount}','time_scaled',3,amount)
    for arm,amount in itertools.product((.10,.20),(.05,.10,.15)):add(f'trail_arm{arm}_{amount}','trailing',4,amount,arm=arm)
    for amount,confirm in itertools.product((.25,.5,1.),(1,2)):add(f'invalidation_{amount}_{confirm}','invalidation',5,amount,confirm=confirm)
    for col,margin in itertools.product((8,11,12),(0,.02,.05)):add(f'probability_{col}_{margin}','probability',6,col=col,margin=margin)
    return entries,exits
def earray(e):return np.array([e[k] for k in ('policy','rem','band')],float)
def xarray(e):return np.array([e[k] for k in ('mode','amount','activate','confirm','arm','model_col','margin')],float)
def select():
    x,mm=load();ee,ex=configurations();cc=[dict(id=e['id']*len(ex)+v['id'],entry=e,exit=v) for e in ee for v in ex];save('configurations.json',cc)
    counts=collections.Counter(co(m['close']) for m in mm);y=np.array([m['result'] for m in mm]);cohort=np.array([co(m['close']) for m in mm]);out=screen(x,y,cohort,np.array([earray(e) for e in ee]),np.array([xarray(e) for e in ex]));np.save(P/'early_screen.npy',out)
    def eligible(c):return all(out[c['id'],i,0]>=.98*counts[i] for i in (0,1)) and c['entry']['policy'] not in (2,3)
    def score(c):return min(out[c['id'],i,1]/max(1,out[c['id'],i,0]) for i in (0,1))
    selected=[]
    for family in dict.fromkeys(v['family'] for v in ex):
        candidates=[c for c in cc if c['exit']['family']==family and eligible(c)];passing=[c for c in candidates if score(c)>0];ranked=sorted(passing or candidates,key=score,reverse=True)
        # Retain at most two genuinely distinct entry policies per family.
        chosen=[]
        for c in ranked:
            if c['entry']['policy'] in [v['entry']['policy'] for v in chosen]:continue
            chosen.append(c)
            if len(chosen)==2:break
        selected.extend(chosen)
    controls=[c for c in cc if c['entry']['policy']==0 and c['entry']['band']==0 and c['entry']['rem'] in (14,10,6) and (c['exit']['name'] in ('hold','loss_0.05','loss_0.1','loss_0.15','salvage_0.1','salvage_0.15'))]
    ids=sorted({c['id'] for c in selected+controls});save('frozen_selection.json',dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=sha(P/'PLAN.md'),configurations_sha256=sha(P/'configurations.json'),trials=len(cc),entry_policies=len(ee),exit_policies=len(ex),selected_ids=ids,family_leaders=[c['id'] for c in selected],control_ids=[c['id'] for c in controls],early_positive_count=int(sum(eligible(c) and score(c)>0 for c in cc)),leaders=[dict(config=c,early=out[c['id'],:2].tolist()) for c in selected]))
    print('Grid',len(cc),'entries',len(ee),'exits',len(ex),'passing',sum(eligible(c) and score(c)>0 for c in cc),'frozen',ids,flush=True)
    for c in selected:print(c['id'],c['entry']['policy_name'],c['entry']['rem'],c['entry']['band'],c['exit']['name'],'early',out[c['id'],:2,1].tolist(),flush=True)
def rows(c,x,mm,delay=0):
    out=[];ec=c['entry'];ex=xarray(c['exit'])
    for i,m in enumerate(mm):
        ent=entry(x[i],ec['policy'],ec['rem'],ec['band'],delay)
        if ent[0]<0:continue
        j,side,price,fallback=ent;xp,k,sold,armed,trigger=exit_trade(x[i],m['result'],ent,ex,delay);debit=float(cash(price,True));credit=float(cash(xp,False)) if sold else xp;won=int(m['result']==side);T=m['start']+60*(j+1)
        xt=m['start']+60*(k+1)+(60 if delay else 3) if sold else max(m['close']+1,m['settled'])
        out.append(dict(ticker=m['ticker'],cohort=PERIODS[co(m['close'])],close=m['close'],entered=T+(60 if delay else 3),decision=T,entry_index=int(j),side=int(side),entry=float(price),exit=float(xp),exit_ts=int(xt),sold=bool(sold),trigger_index=int(trigger),armed=bool(armed),forecast_fallback=bool(fallback),debit=debit,credit=credit,legs=1+int(sold),settlement_win=won,pnl=debit+credit,hold_pnl=debit+won))
    return out
def metrics(rr,den,ci,stress=.0025):
    rr=sorted(rr,key=lambda r:(r['exit_ts'],r['ticker']));p=np.array([r['pnl']-stress*r['legs'] for r in rr]);eq=np.r_[0,np.cumsum(p)];n=len(rr);daily={dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat():0. for t in range(BOUNDS[ci],BOUNDS[ci+1],86400)}
    for r,v in zip(rr,p):daily[dt.datetime.fromtimestamp(r['close'],dt.timezone.utc).date().isoformat()]+=float(v)
    saved=[r for r in rr if r['sold'] and not r['settlement_win']];sacrificed=[r for r in rr if r['sold'] and r['settlement_win']];benefit=sum(r['credit']-stress for r in saved);damage=sum(1-r['credit']+stress for r in sacrificed);hold=sum(r['hold_pnl']-stress for r in rr)
    dd=np.array(list(daily.values()));rng=np.random.default_rng(41004);bs=dd[rng.integers(0,len(dd),size=(10000,len(dd)))].sum(axis=1)
    return dict(trades=n,windows=den,coverage=n/den if den else 0,net=round(float(p.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),cash_wins=int((p>1e-10).sum()),cash_losses=int((p< -1e-10).sum()),cash_win_rate=float((p>1e-10).mean()) if n else None,original_wins=sum(r['settlement_win'] for r in rr),original_win_rate=sum(r['settlement_win'] for r in rr)/n if n else None,mean_cash_win=float(p[p>0].mean()) if (p>0).any() else None,mean_cash_loss=float(p[p<0].mean()) if (p<0).any() else None,mean_entry=float(np.mean([r['entry'] for r in rr])) if n else None,early_exits=sum(r['sold'] for r in rr),saved_losers=len(saved),stopped_winners=len(sacrificed),net_recovered_from_losers=round(benefit,6),net_forfeited_on_winners=round(damage,6),exit_improvement=round(benefit-damage,6),hold_same_entries=round(hold,6),mean_net_recovery_on_stopped_loser=benefit/len(saved) if saved else None,forecast_fallbacks=sum(r['forecast_fallback'] for r in rr),daily=daily,worst_day=min(daily.values()),day_bootstrap90=np.quantile(bs,[.05,.95]).tolist())
def evaluate():
    x,mm=load();cc=json.loads((P/'configurations.json').read_text());f=json.loads((P/'frozen_selection.json').read_text());assert f['plan_sha256']==sha(P/'PLAN.md') and f['configurations_sha256']==sha(P/'configurations.json');out={}
    for cid in f['selected_ids']:
        c=cc[cid];r=rows(c,x,mm);d=rows(c,x,mm,1)
        with gzip.open(P/f'trades_{cid}.json.gz','wt') as file:json.dump(r,file,separators=(',',':'))
        pp={}
        for ci,cohort in enumerate(PERIODS):
            den=sum(co(m['close'])==ci for m in mm);rr=[v for v in r if v['cohort']==cohort];dr=[v for v in d if v['cohort']==cohort]
            pp[cohort]=dict(base=metrics(rr,den,ci),cost1c=metrics(rr,den,ci,.01),cost2c=metrics(rr,den,ci,.02),delay60=metrics(dr,den,ci))
        out[cid]=dict(config=c,periods=pp)
        print(cid,c['entry']['policy_name'],c['entry']['rem'],c['entry']['band'],c['exit']['name'],[(cohort,pp[cohort]['base']['net']) for cohort in PERIODS[2:]],flush=True)
    save('results.json',out)
if __name__=='__main__':{'prepare':prepare,'select':select,'evaluate':evaluate}[sys.argv[1]]()
