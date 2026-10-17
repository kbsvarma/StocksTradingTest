"""One mandatory decision per listed quarter/period, without an edge gate."""
from core import *
from source_adapter import prepare,at
import numpy as np

SRC=P.parent/'kalshi_nfl_nhl_frequency_2026_10_10'
POLICIES=['favorite','underdog','leader','pregame','tie_yes','tie_no','model','blend']
TIMES={'NFL':{'early':600,'middle':450,'late':300},'NHL':{'early':900,'middle':600,'late':300}}
SCENARIOS={'base':(30,'open',.01),'cost2c':(30,'open',.02),'delay60':(30,'close',.01),'source90':(90,'open',.01),'both_delays':(90,'close',.01)}

def loadgame(league,et,ms,mi):
    s='KX'+league+'GAME';d=SRC/'data'/s
    vals=prepare(ms,read(d/(et+'_candles.json.gz')),mi,s,read(d/(et+'_espn.json.gz')))
    states,stamps,end,home,away,tm,csmap,pm,*_=vals
    return {'league':league,'event':et,'mi':mi,'states':states,'stamps':stamps,'end':end,'home':home,'away':away,'pregame':pm}
def partinfo(g,per):
    pst=[r for r in g['states'] if r['period']==per]
    if not pst:return None
    before=[r for r in g['states'] if r['period']<per and r['wall']<pst[0]['wall']]
    if per>1 and not before:return None
    h0,a0=(0,0) if per==1 else (before[-1]['home'],before[-1]['away'])
    return {'h0':h0,'a0':a0,'start':pst[0]['wall'],'end':max(r['wall'] for r in pst),
            'final_home':pst[-1]['home']-h0,'final_away':pst[-1]['away']-a0}
def features(g,part,r):
    diff=(r['home']-part['h0'])-(r['away']-part['a0']);frac=r['clock']/(900 if g['league']=='NFL' else 1200)
    ph=g['pregame'].get(g['home'],.5);pa=g['pregame'].get(g['away'],.5)
    p=min(.99,max(.01,ph/max(ph+pa,.01)));logit=math.log(p/(1-p))
    poss=1 if r.get('post_possession')==g['home'] else -1 if r.get('post_possession')==g['away'] else 0
    return [diff,diff/math.sqrt(frac+.05),frac,logit,poss,poss*frac,r['period']]
def firsttrigger(g,part,per,threshold,lag):
    for t in range(math.ceil((part['start']+lag)/60)*60,part['end'],60):
        r=at(g['states'],g['stamps'],t,lag)
        if r and r['period']==per and 0<r['clock']<=threshold and t-r['wall']<=120:return t
    return None
def fitmodels():
    train=[];valid=[];goals=[]
    for league in ['NFL','NHL']:
        s='KX'+league+'GAME';d=SRC/'data'/s;groups=defaultdict(list)
        for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
        for et,ms in groups.items():
            mi=read(d/(et+'_milestone.json'));start=ts(mi['start_date'])
            if mi['details']['season_type'] not in ('REG','POST') or start>=ts('2026-04-01T04:00:00Z'):continue
            if league=='NHL' and start<ts('2026-01-01T05:00:00Z'):continue
            g=loadgame(league,et,ms,mi)
            cutoff=ts('2025-12-01T05:00:00Z') if league=='NFL' else ts('2026-02-15T05:00:00Z')
            for per in range(1,5 if league=='NFL' else 4):
                part=partinfo(g,per)
                if not part:continue
                target=0 if part['final_home']>part['final_away'] else 1 if part['final_away']>part['final_home'] else 2
                if league=='NHL' and start<cutoff:goals.append([part['final_home'],part['final_away']])
                if league!='NFL':continue
                for threshold in TIMES[league].values():
                    t=firsttrigger(g,part,per,threshold,30)
                    if t is None:continue
                    r=at(g['states'],g['stamps'],t,30);row={'event':et,'x':features(g,part,r),'y':target,'settled':max(ts(m['settlement_ts']) for m in ms)}
                    (train if row['settled']<cutoff else valid).append(row)
    raw=np.asarray([r['x'] for r in train]);mean=raw.mean(0);scale=raw.std(0);scale[scale==0]=1
    xx=np.column_stack([np.ones(len(raw)),(raw-mean)/scale]);yy=np.eye(3)[[r['y'] for r in train]];weights=np.zeros((xx.shape[1],3))
    rate=1/(np.linalg.norm(xx,2)**2/len(xx)+10/len(xx));penalty=np.ones_like(weights)*10/len(xx);penalty[0]=0
    for iteration in range(4000):
        zz=xx@weights;zz-=zz.max(1,keepdims=True);pp=np.exp(zz);pp/=pp.sum(1,keepdims=True)
        grad=xx.T@(pp-yy)/len(xx)+penalty*weights;weights-=rate*grad
        if np.max(np.abs(grad))<1e-7:break
    model=weights;scaler=(mean,scale)
    nhl_rates=np.mean(goals,axis=0).tolist()
    meta={'NFL':{'training_rows':len(train),'validation_rows':len(valid),'train_max_settlement':max(r['settled'] for r in train),'C':.1,'weights_with_intercept':weights.tolist(),'classes':[0,1,2],'scale_mean':mean.tolist(),'scale':scale.tolist(),'iterations':iteration+1,'max_gradient':float(np.max(np.abs(grad)))},
          'NHL':{'training_periods':len(goals),'goals_per_period_home_away':nhl_rates,'training_end_exclusive':'2026-02-15T05:00:00Z'},
          'note':'NFL three-class quarter-score model fitted before Dec1,2025. NHL fixed Poisson goal model fitted before Feb15,2026. These are forecasts, not proof of pricing advantage.'}
    if valid:
        zz=np.column_stack([np.ones(len(valid)),(np.asarray([r['x'] for r in valid])-mean)/scale])@weights;meta['NFL']['validation_accuracy']=float(np.mean(np.argmax(zz,axis=1)==np.array([r['y'] for r in valid])))
    save(P/'models.json',meta)
    return model,scaler,nhl_rates
def probabilities(g,part,r,fitted):
    model,scaler,rates=fitted;x=features(g,part,r)
    if g['league']=='NFL':
        zz=np.r_[1,(np.array(x)-scaler[0])/scaler[1]]@model;pp=np.exp(zz-zz.max());return (pp/pp.sum()).tolist()
    frac=r['clock']/1200;strength=math.exp(.25*x[3]);total=sum(rates);share=rates[0]*strength/(rates[0]*strength+rates[1]/strength)
    h=max(.00001,total*share*frac);a=max(.00001,total*(1-share)*frac);diff=x[0]
    def poisson(mu):
        p=[math.exp(-mu)]
        for k in range(1,40):p.append(p[-1]*mu/k)
        return np.array(p)
    mass=np.outer(poisson(h),poisson(a));d=diff+np.arange(40)[:,None]-np.arange(40)[None,:]
    pp=np.array([mass[d>0].sum(),mass[d<0].sum(),mass[d==0].sum()]);return (pp/pp.sum()).tolist()
def choose(policy,options,g,part,r,probs):
    yes=[x for x in options if x['side']=='yes'];by={x['label']:x for x in yes}
    if not options:return None
    if policy in ('favorite','underdog','blend') and set(by)!={0,1,2}:return None
    mids=np.array([by[k]['mid'] if k in by else 0 for k in [0,1,2]]);market=mids/max(mids.sum(),.001)
    if policy in ('model','blend'):
        pp=np.array(probs) if policy=='model' else (np.array(probs)+market)/2
        return max(options,key=lambda x:((pp[x['label']] if x['side']=='yes' else 1-pp[x['label']])-x['ask']-fee(x['ask'],1,x['mult']),-x['label'],x['side']))
    if policy=='favorite':label=int(np.argmax(mids))
    elif policy=='underdog':label=int(np.argmin(mids))
    elif policy=='leader':
        dh=r['home']-part['h0'];da=r['away']-part['a0'];label=0 if dh>da else 1 if da>dh else 2
    elif policy=='pregame':label=0 if g['pregame'].get(g['home'],.5)>=g['pregame'].get(g['away'],.5) else 1
    else:label=2
    side='no' if policy=='tie_no' else 'yes'
    return next((x for x in options if x['label']==label and x['side']==side),None)

def main():
    fitted=fitmodels();candidates=defaultdict(list);skips=defaultdict(Counter);windows=[];checks=Counter()
    index=read(P/'segment_index.json')
    for n,info in enumerate(index,1):
        league=info['league'];g=loadgame(league,info['event'],info['full_markets'],info['milestone']);cm=read(P/'candles'/(g['event']+'.json.gz'))
        cmap={ticker:{c['end_period_ts']:c for c in bars} for ticker,bars in cm.items()}
        for cs in cmap.values():
            for t,c in cs.items():c['_volume10']=sum(float(cs.get(tt,{}).get('volume_fp') or cs.get(tt,{}).get('volume') or 0) for tt in range(t-540,t+60,60))
        for per in range(1,5 if league=='NFL' else 4):
            items=[i['market'] for i in info['segments'] if i['period']==per];part=partinfo(g,per);owner=league+'/'+g['mi']['id']+'/segment'+str(per)
            window={'owner':owner,'asset':league,'period':per,'date':day(ts(g['mi']['start_date'])),'event':g['event'],'status':'ready'}
            if not part or len(items)!=3:window['status']='missing_source_or_market';windows.append(window);continue
            windows.append(window);labels={}
            for m in items:
                team=next(iter(m.get('custom_strike',{}).values()),None)
                label=0 if team==g['home'] else 1 if team==g['away'] else 2 if m['ticker'].endswith('-TIE') else None
                if label is None:raise ValueError('Unknown segment outcome '+m['ticker'])
                labels[m['ticker']]=label
                actual=0 if part['final_home']>part['final_away'] else 1 if part['final_away']>part['final_home'] else 2
                if pay(m,'yes')!=int(label==actual):raise ValueError('Period settlement mismatch '+m['ticker'])
                checks['settlements_matched']+=1
            for timing,threshold in TIMES[league].items():
                for scenario,(lag,mode,cost) in SCENARIOS.items():
                    if scenario=='cost2c':continue
                    first=firsttrigger(g,part,per,threshold,lag)
                    for policy in POLICIES:
                        key=league+'/'+timing+'/'+policy+'/'+scenario;reason='no_checkpoint';row=None
                        if first is not None:
                            for t in range(first,min(first+120,part['end']-1)+1,60):
                                r=at(g['states'],g['stamps'],t,lag)
                                if not r or r['period']!=per or not 0<r['clock']<=threshold or t-r['wall']>120:reason='stale_or_ended';continue
                                options=[]
                                for m in items:
                                    cs=cmap.get(m['ticker'],{});mult=float(read(P/'catalogue'/(m['event_ticker'].split('-')[0]+'_series.json'))['fee_multiplier'])
                                    for side in ['yes','no']:
                                        q=qte(cs.get(t),side)
                                        if q:
                                            b,a=q;options.append({'ticker':m['ticker'],'side':side,'label':labels[m['ticker']],'ask':a,'bid':b,'mid':(a+b)/2,'mult':mult,'_m':m,'_cs':cs})
                                probs=probabilities(g,part,r,fitted);x=choose(policy,options,g,part,r,probs)
                                if x is None:reason='incomplete_outcome_quotes';continue
                                if not .01<=x['ask']<=.99 or x['ask']-x['bid']>.05000001:reason='price_or_spread';continue
                                if x['_cs'][t]['_volume10']<5:reason='volume';continue
                                q2=qte(x['_cs'].get(t+60),x['side'],mode);entered=t+(3 if mode=='open' else 60);r2=at(g['states'],g['stamps'],entered,lag)
                                if not q2:reason='execution_quote';continue
                                if not r2 or r2['period']!=per or entered-r2['wall']>120 or r2['clock']<=0:reason='execution_source';continue
                                m=x['_m'];entry=max(x['ask'],q2[1]);cap=min(.99,x['ask']+.02)
                                if entry>cap+1e-8:reason='execution_cap';continue
                                if entered>=min(part['end'],ts(m['close_time']),ts(m['settlement_ts'])):reason='after_close';continue
                                row={k:v for k,v in x.items() if not k.startswith('_')};row.update(owner=owner,asset=league,kind='segment',event=m['event_ticker'],period=per,t=t,entered=entered,entry=entry,cap=cap,settled=ts(m['settlement_ts']),payout=pay(m,x['side']),close=ts(m['close_time']),segment_end=part['end'],source_wall=r['wall'],source_age=t-r['wall'],execution_source_wall=r2['wall'],lag=lag,clock=r['clock'],segment_home=r['home']-part['h0'],segment_away=r['away']-part['a0'],probabilities=probs,trigger=first,volume10=x['_cs'][t]['_volume10'],date=window['date'])
                                break
                        if row:candidates[key].append(row)
                        else:skips[key][reason]+=1
        if n%20==0:print('SEGMENTS',n,'/',len(index),flush=True)
    save(P/'segment_candidates.json.gz',candidates);save(P/'segment_windows.json',windows);save(P/'segment_skips.json',{k:dict(v) for k,v in skips.items()});save(P/'segment_checks.json',dict(checks))
    replay(candidates,windows)
def replay(candidates,windows):
    result={};ledgers={}
    for league in ['NFL','NHL']:
        for timing in ['early','middle','late']:
            for policy in POLICIES:
                for scenario,(_,_,friction) in SCENARIOS.items():
                    rows=candidates.get(league+'/'+timing+'/'+policy+'/'+('base' if scenario=='cost2c' else scenario),[])
                    for part in ['all','before_oct','october']:
                        pick=lambda d:part=='all' or (d<'2026-10-01' if part=='before_oct' else d>='2026-10-01')
                        den=sum(w['asset']==league and pick(w['date']) for w in windows);rr=[r for r in rows if pick(r['date'])]
                        for qty in [1,5]:record(result,ledgers,f'{league}/{timing}/{policy}/{scenario}/{part}/q{qty}',rr,den,qty,friction)
    save(P/'segment_results.json',result);save(P/'segment_ledgers.json.gz',ledgers)
    for league in ['NFL','NHL']:
        rows=[(k,v) for k,v in result.items() if k.startswith(league+'/') and '/base/all/q1' in k]
        for k,v in sorted(rows,key=lambda kv:kv[1]['net'],reverse=True)[:8]:print(k,{z:v[z] for z in ['windows','trades','wins','losses','net','participation','drawdown']},flush=True)
if __name__=='__main__':main()
