from conditional import *

_,events=load();intents=[];counts=Counter()
for e in events.values():
    start=e['close']-900;t=start+120
    if not START<=start<END:continue
    choices=[]
    for m in e['items']:
        cs=e['cm'].get(m['ticker'],{});q=quote(cs.get(start))
        if not q or not .1<=sum(q)/2<=.9 or q[1]-q[0]>.05000001:continue
        if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if start-600<tt<=start)<5:continue
        choices.append((abs(sum(q)/2-.5),m,cs,q))
    if not choices:continue
    _,m,cs,before=min(choices,key=lambda x:(x[0],x[1]['ticker']));q=quote(cs.get(t));counts['initial_windows']+=1
    if not q:counts['missing_decision_quote']+=1;continue
    delta=(sum(q)-sum(before))/2
    if abs(delta)<.04-1e-8:counts['move_too_small']+=1;continue
    side='yes' if delta>0 else 'no';ask=sq(q,side)[1]
    if not .2<=ask<.95 or q[1]-q[0]>.02000001:counts['price_or_spread']+=1;continue
    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:counts['volume']+=1;continue
    intents.append(dict(asset=e['asset'],event=m['event_ticker'],ticker=m['ticker'],t=t,close=e['close'],settled=e['settled'],
        side=side,signal_ask=ask,cap=min(.95,ask+.01),model_p=sq(q,side)[0],won=m['result']==side,delta=delta,
        prior_mid=sum(before)/2,current_mid=sum(q)/2,future={mode:quote(cs.get(t+60),phase) for mode,phase in [('base','open'),('delay60','close')]}))
results={};ledgers={}
for mode,friction in [('base',.01),('base',.02),('delay60',.01),('delay60',.02),('cap',.01)]:
    candidates=[];skips=Counter()
    for r in intents:
        q=r['future']['base' if mode=='cap' else mode]
        if q is None:skips['missing_next_quote']+=1;continue
        entry=max(r['signal_ask'],sq(q,r['side'])[1])
        if entry>r['cap']+1e-8:skips['cap']+=1;continue
        if mode=='cap':entry=r['cap']
        candidates.append(dict({k:v for k,v in r.items() if k!='future'},entry=entry,entered=r['t']+(60 if mode=='delay60' else 10),fee=fee(entry,1)))
    ledger,risk=portfolio(candidates,friction);key=f'{mode}/cost{int(100*friction)}c';ledgers[key]=ledger
    results[key]={'all':describe(ledger),'earlier':describe([r for r in ledger if r['t']<SPLIT],START,SPLIT),
        'later':describe([r for r in ledger if r['t']>=SPLIT],SPLIT,END),'cities':{c:metrics([r for r in ledger if r['asset']==c]) for c in CITIES},
        'risk':risk,'execution_skips':dict(skips),'intents':len(intents)}
    print(key,len(ledger),results[key]['all']['net'],flush=True)
save(P/'repricing_results.json',results);save(P/'repricing_ledgers.json.gz',ledgers);save(P/'repricing_intents.json.gz',intents);save(P/'repricing_coverage.json',dict(counts))
