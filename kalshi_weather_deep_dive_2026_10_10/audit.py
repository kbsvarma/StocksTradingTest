"""Read-only research: audit archived weather signals, execution ordering and cities."""
import sys,json,statistics,math,random,hashlib
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'kalshi_broad_review_2026_10_10'
sys.path.insert(0,str(BASE))
from common import read,save,ts,START,END
from weather_model import forecast,CITIES,SPLIT
from replay_candidates import quote,sq,fee,portfolio,metrics,day

def choose(choices):
    # Uses only fields available at decision; future entry price is excluded.
    return max(choices,key=lambda x:(x['model_edge'],-x['ask'],x['ticker']))

def describe(rows):
    m=metrics(rows);n=len(rows)
    if not n:return m
    win=m['wins']/n;z=1.95996398454;den=1+z*z/n
    lower=(win+z*z/(2*n)-z*math.sqrt(win*(1-win)/n+z*z/(4*n*n)))/den
    days=[m['daily'].get(day(SPLIT+i*86400),0) for i in range(7)]
    rng=random.Random(20261010);boot=sorted(sum(rng.choice(days) for _ in days) for __ in range(10000))
    m.update(win_rate=win,entries_per_day=n/7,mean_price=statistics.mean(r['entry'] for r in rows),mean_model_probability=statistics.mean(r['model_p'] for r in rows),break_even=win-m['net']/(5*n),wilson95_lower_independent=lower,day_bootstrap95=[boot[250],boot[9750]],without_best_day=m['net']-max(days),mean_win=statistics.mean(r['net'] for r in rows if r['won']) if any(r['won'] for r in rows) else None,mean_loss=statistics.mean(r['net'] for r in rows if not r['won']) if any(not r['won'] for r in rows) else None,worst_trade=min(r['net'] for r in rows),net_if_one_win_became_loss=m['net']-5,horizon={str(h):metrics([r for r in rows if r['horizon']==h]) for h in (30,15,5)},side={side:metrics([r for r in rows if r['side']==side]) for side in ('yes','no')},first4days=metrics([r for r in rows if r['close']<SPLIT+4*86400]),last3days=metrics([r for r in rows if r['close']>=SPLIT+4*86400]),settlement_delay_minutes={'min':min((r['settled']-r['close'])/60 for r in rows),'median':statistics.median((r['settled']-r['close'])/60 for r in rows),'max':max((r['settled']-r['close'])/60 for r in rows)})
    return m

spec=read(BASE/'weather_paper_spec.json');old=read(BASE/'weather_model_ledgers.json.gz')
report={'spec_sha256':hashlib.sha256((BASE/'weather_paper_spec.json').read_bytes()).hexdigest(),'prior':{},'strict':{},'selection_differences':[],'funnel':{},'forecast_errors':{},'loss_details':[]}
pool=defaultdict(list);funnel=defaultdict(Counter);errdiag={};ledger={}
for label in ('fees1c','fees2c','delay1c'):
    rows=old[f'ALL/persistence/lag10/{label}'];report['prior'][label]={'ALL':describe(rows),**{c:describe([r for r in rows if r['asset']==c]) for c in CITIES}}

for city in CITIES:
    d=BASE/'replays'/city;meta=read(d/'series.json');ms=read(d/'markets.json');events=defaultdict(list)
    for m in ms:events[m['event_ticker']].append(m)
    bars={};bad=set()
    for f in sorted(d.glob('*_source.json.gz')):
        for p in read(f)['live_data']['details']['timeseries']:
            t=int(p['t'])//1000;v=p['v']
            if v.get('temp_f') is None or t%60 or v.get('status') not in ('normal','degraded'):continue
            val=(float(v['temp_f']),v['status'])
            if t in bars and bars[t]!=val:bad.add(t)
            bars[t]=val
    for t in bad:bars.pop(t,None)
    data=[]
    for event,items in sorted(events.items(),key=lambda x:x[1][0]['close_time']):
        m=items[0];cl=ts(m['close_time']);final=float(m['expiration_value'])
        cm=read(d/(event+'_candles.json.gz'));cs={k:{v['end_period_ts']:v for v in vs} for k,vs in cm.items()}
        data.append((event,items,cl,final,cs))
    errs={h:spec['calibrations'][city][str(h)]['errors_f'] for h in (30,15,5)}
    for h in (30,15,5):
        vals=[]
        for event,items,cl,final,cs in data:
            if not SPLIT<=cl<END:continue
            pred=forecast(bars,cl-h*60,cl,'persistence',10)
            if pred is not None:vals.append(final-pred)
        errdiag[f'{city}/{h}']={'training_n':len(errs[h]),'test_n':len(vals),'training_mae':statistics.mean(abs(e) for e in errs[h]),'test_mae':statistics.mean(abs(e) for e in vals),'training_bias':statistics.mean(errs[h]),'test_bias':statistics.mean(vals)}
    for event,items,cl,final,csmap in data:
        if not SPLIT<=cl<END:continue
        funnel[city]['events']+=1
        for h in (30,15,5):
            t=cl-h*60;pred=forecast(bars,t,cl,'persistence',10);funnel[city]['horizon_checks']+=1
            if pred is None:funnel[city]['source_missing']+=1;continue
            choices=[]
            for m in items:
                if not ts(m['open_time'])<=t<cl:continue
                cs=csmap.get(m['ticker'],{});q=quote(cs.get(t))
                if q is None:continue
                funnel[city]['contract_quotes']+=1
                if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
                py=(sum(pred+e>float(m['floor_strike']) for e in errs[h])+1)/(len(errs[h])+2)
                for side,p in [('yes',py),('no',1-py)]:
                    bid,ask=sq(q,side)
                    if not .60<=ask<.95 or ask-bid>.02000001:continue
                    edge=p-ask-fee(ask,meta['fee_multiplier'])/5-.01
                    if edge<.02:continue
                    x=dict(category='weather',profile='persistence',series=city,asset=city,event=event,ticker=m['ticker'],side=side,t=t,settled=ts(m['settlement_ts']),close=cl,ask=ask,cap=min(.95,ask+.01),won=m['result']==side,model_p=p,model_edge=edge,forecast=pred,lag=10,horizon=h,calibration_n=len(errs[h]),source_last=t-600,final_value=final,strike=float(m['floor_strike']))
                    choices.append(x)
            if not choices:continue
            funnel[city]['qualifying_horizons']+=1;funnel[city]['qualifying_sides']+=len(choices)
            pick=choose(choices)
            for phase,label in [('open','fees1c'),('open','fees2c'),('close','delay1c')]:
                executable=[]
                for candidate in choices:
                    nxt=quote(csmap.get(candidate['ticker'],{}).get(t+60),phase)
                    if nxt is None:continue
                    ep=max(candidate['ask'],sq(nxt,candidate['side'])[1])
                    if ep<=candidate['cap']+1e-8:
                        executable.append(dict(candidate,entry=ep,fee=fee(ep,meta['fee_multiplier']),entered=t+60 if phase=='close' else t+3))
                actual=next((x for x in executable if (x['ticker'],x['side'])==(pick['ticker'],pick['side'])),None)
                prior=max(executable,key=lambda x:(x['model_edge'],-x['entry'],x['ticker'])) if executable else None
                if prior and (actual is None or prior['ticker']!=actual['ticker'] or prior['side']!=actual['side']):
                    report['selection_differences'].append({'city':city,'event':event,'horizon':h,'label':label,'decision_pick':pick,'old_executable_pick':prior})
                if actual:pool[label].append(actual)
                elif label=='fees1c':funnel[city]['chosen_not_executable']+=1
report['forecast_errors']=errdiag;report['funnel']={c:dict(v) for c,v in funnel.items()}
for label,rows in pool.items():
    stress=.02 if label=='fees2c' else .01
    accepted,blocked=portfolio(rows,stress);unconstrained,_=portfolio(rows,stress,False)
    report['strict'][label]={'ALL':describe(accepted),'blocked':blocked,'unconstrained':describe(unconstrained),'cities':{c:describe([r for r in accepted if r['asset']==c]) for c in CITIES},'standalone_cities':{c:describe(portfolio([r for r in rows if r['asset']==c],stress)[0]) for c in CITIES}}
    ledger[label]=accepted
    if label=='fees1c':report['loss_details']=[r for r in accepted if not r['won']]
# Confirm the selector has no dependence on future prices or realized outcomes.
cases=[dict(ticker='A',ask=.7,model_edge=.1),dict(ticker='B',ask=.8,model_edge=.05)]
assert choose(cases)['ticker']=='A'
assert choose([dict(x,entry=(.99 if x['ticker']=='A' else .1),won=x['ticker']=='B') for x in cases])['ticker']=='A'
save(ROOT/'city_audit.json',report);save(ROOT/'strict_ledgers.json.gz',ledger)
print(json.dumps({'selection_differences':len(report['selection_differences']),'strict':{k:{'ALL':v['ALL'],'cities':v['cities']} for k,v in report['strict'].items()}},indent=2))
