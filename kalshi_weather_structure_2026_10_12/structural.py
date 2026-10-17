"""Cross-strike minimum-payout audit with both buy prices and both fees."""
from conditional import *

def main():
    _,events=load();signals=[];counts=Counter();near=[]
    for event,e in events.items():
        if not START<=e['close']-60<END:continue
        items=sorted(e['items'],key=lambda m:float(m['floor_strike']))
        times=sorted(set(t for cs in e['cm'].values() for t in cs if e['close']-40*60<=t<e['close']))
        for t in times:
            qs=[]
            for m in items:
                cs=e['cm'].get(m['ticker'],{});q=quote(cs.get(t))
                if q is None or q[1]-q[0]>.05000001:continue
                if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
                qs.append((m,q,cs))
            options=[]
            for i,(ml,ql,cl) in enumerate(qs):
                for mh,qh,ch in qs[i+1:]:
                    if float(ml['floor_strike'])>=float(mh['floor_strike']):continue
                    yes,no=ql[1],1-qh[0]
                    if not .1<=yes<=.95 or not .1<=no<=.95:continue
                    counts['comparable_pair_minutes']+=1
                    surplus=1-yes-no-(fee(yes,1)+fee(no,1))/5-.02
                    if surplus>=-.01:
                        near.append(dict(event=event,t=t,lower=ml['ticker'],upper=mh['ticker'],yes=yes,no=no,surplus=surplus))
                    if surplus<.01:continue
                    counts['qualified_pair_minutes']+=1
                    options.append((surplus,ml,mh,yes,no,cl,ch))
            if not options:continue
            sur,ml,mh,yes,no,cl,ch=max(options,key=lambda x:(x[0],x[1]['ticker'],x[2]['ticker']))
            r=dict(asset=e['asset'],event=event,t=t,close=e['close'],settled=e['settled'],lower=ml['ticker'],upper=mh['ticker'],
                lower_strike=ml['floor_strike'],upper_strike=mh['floor_strike'],signal_yes=yes,signal_no=no,
                payout=int(ml['result']=='yes')+int(mh['result']=='no'),surplus=sur,
                future={mode:[quote(cl.get(t+60),phase),quote(ch.get(t+60),phase)] for mode,phase in [('base','open'),('delay60','close')]})
            assert r['payout']>=1
            signals.append(r)
    ledgers={};results={}
    for mode,friction in [('base',.01),('base',.02),('delay60',.01),('delay60',.02)]:
        accepted=[];active=[];cash=100.;daily=defaultdict(float);seen=set();skips=Counter()
        for r in sorted(signals,key=lambda r:(r['t'],r['asset'])):
            when=r['t']+(10 if mode=='base' else 60)
            if when>=r['close']:skips['closed']+=1;continue
            for a in active:
                if a['settled']<=when:cash+=5*a['payout'];daily[date(a['settled'])]+=a['net']
            active=[a for a in active if a['settled']>when]
            if r['event'] in seen:continue
            lo,hi=r['future'][mode]
            if lo is None or hi is None:skips['missing_quote']+=1;continue
            yes=max(r['signal_yes'],lo[1]);no=max(r['signal_no'],1-hi[0])
            fees=fee(yes,1)+fee(no,1);cost=5*(yes+no)+fees+10*friction
            if cost>4.95+1e-8:skips['no_longer_positive_minimum']+=1;continue
            reserved=sum(a['cost'] for a in active)
            if len(active)*10+10>25 or reserved+cost>25 or daily[date(when)]-reserved-cost < -20 or cash-cost<2:
                skips['portfolio_limit']+=1;continue
            a={k:v for k,v in r.items() if k!='future'}
            a.update(entered=when,entry_yes=yes,entry_no=no,fee=fees,friction=friction,cost=cost,net=5*r['payout']-cost,won=True,
                model_p=1.0,minimum_net=5-cost)
            cash-=cost;active.append(a);accepted.append(a);seen.add(r['event'])
        key=f'{mode}/cost{int(friction*100)}c_per_leg';ledgers[key]=accepted;results[key]={'all':describe(accepted),'execution_skips':dict(skips)}
        print(key,len(accepted),results[key]['all']['net'],flush=True)
    save(P/'structural_results.json',{'coverage':dict(counts),'near_flat_pairs':len(near),'signals':len(signals),'results':results})
    save(P/'structural_signals.json.gz',signals);save(P/'structural_near.json.gz',near);save(P/'structural_ledgers.json.gz',ledgers)

if __name__=='__main__':main()
