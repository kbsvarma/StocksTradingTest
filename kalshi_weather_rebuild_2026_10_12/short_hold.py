"""Frozen alternative weather mechanisms. Cached public research data only."""
from pathlib import Path
from collections import defaultdict,Counter
import sys,math,time,hashlib
import numpy as np
from rebuild import P,OLD,EXT,CITIES,ts,read,save,quote,sq,fee,metrics,day,summarize
START=ts('2026-09-19T04:00:00Z');SPLIT=ts('2026-10-10T04:00:00Z')
END=ts('2026-10-12T04:00:00Z') if '--extension' in sys.argv else SPLIT

def collect():
    intents=defaultdict(list);coverage={}
    for city in CITIES:
        d=EXT/'data'/city
        if not d.exists():d=OLD/'replays'/city
        ms=read(d/'markets.json');meta=read(d/'series.json');bars={}
        if (d/'bars.json.gz').exists():bars={int(t):v for t,v in read(d/'bars.json.gz').items()}
        else:
            for p in d.glob('*_source.json.gz'):
                for a in read(p).get('live_data',{}).get('details',{}).get('timeseries',[]):
                    if a.get('v',{}).get('temp_f') is not None:bars[int(a['t'])//1000]=[float(a['v']['temp_f']),a['v'].get('status')]
        newer=P/'extension'/city
        if '--extension' in sys.argv and (newer/'markets.json').exists():
            ms=list({m['ticker']:m for m in ms+read(newer/'markets.json')}.values())
            bars.update({int(t):v for t,v in read(newer/'bars.json.gz').items()})
        groups=defaultdict(list)
        for m in ms:
            if START<=ts(m['close_time'])<=END:groups[m['event_ticker']].append(m)
        cov=Counter(events=len(groups))
        for event,items in groups.items():
            if any(m.get('result') not in ('yes','no') or not m.get('settlement_ts') for m in items):cov['nonbinary_unsettled']+=1;continue
            cl=ts(items[0]['close_time']);cp=d/(event+'_candles.json.gz')
            if not cp.exists():cp=newer/(event+'_candles.json.gz')
            if not cp.exists():cov['missing_candles']+=1;continue
            cm=read(cp);csmap={k:{c['end_period_ts']:c for c in v} for k,v in cm.items()}
            for h in [35,30,25,20,15,10]:
                t=cl-h*60
                if not START<=t<END:continue
                options=defaultdict(list)
                for m in items:
                    cs=csmap.get(m['ticker'],{});q=quote(cs.get(t));before=quote(cs.get(t-300))
                    if q is None or before is None or q[1]-q[0]>.02000001:continue
                    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
                    move=(sum(q)-sum(before))/2
                    if abs(move)<.05-1e-8:continue
                    for design in ['continuation','reversal','temperature_continuation']:
                        sign=1 if move>0 else -1
                        if design=='reversal':sign=-sign
                        if design=='temperature_continuation':
                            last=t-600;keys=range(last-3600,last+1,60)
                            if not all(k in bars for k in keys) or bars[last][1]!='normal':continue
                            delta=bars[last][0]-bars[last-900][0]
                            if sign*delta<.25-1e-8:continue
                        side='yes' if sign>0 else 'no';bid,ask=sq(q,side)
                        if not .40<=ask<.95:continue
                        options[design].append(dict(asset=city,event=event,ticker=m['ticker'],t=t,close=cl,
                            official_settled=ts(m['settlement_ts']),outcome=int(m['result']==side),
                            side=side,ask=ask,move=move,cap=min(ask+.01,.95),horizon=h,fee_multiplier=meta['fee_multiplier']))
                for design,choices in options.items():
                    r=max(choices,key=lambda r:(abs(r['move']),-r['ask'],r['ticker']));cs=csmap[r['ticker']];future={}
                    for label,entry_phase,exit_delay in [('base','open',300),('delay60','close',360)]:
                        eq=quote(cs.get(t+60),entry_phase);xq=quote(cs.get(t+exit_delay))
                        future[label]={'entry':eq,'exit':xq,'exit_delay':exit_delay}
                    intents[design].append(dict(r,future=future))
        coverage[city]=dict(cov)
    save(P/'short_coverage.json',coverage);save(P/'short_intents.json.gz',intents)
    return intents

def port(candidates,stress):
    cash=100.;active=[];accepted=[];seen=set();realized=defaultdict(float);blocks=Counter()
    for r in sorted(candidates,key=lambda r:(r['entered'],r['asset'],r['ticker'])):
        for a in active:
            if a['settled']<=r['entered']:
                cash+=a['proceeds'];realized[day(a['settled'])]+=a['net']
        active=[a for a in active if a['settled']>r['entered']]
        if r['event'] in seen:continue
        cost=5*r['entry']+r['entry_fee']+5*stress
        reserve=max(4.75,cost+5*stress)
        risk=reserve+sum(a['reserve'] for a in active)
        reason=''
        if any(a['asset']==r['asset'] for a in active):reason='city_pending'
        elif len(active)>=5 or risk>25+1e-8:reason='exposure'
        elif realized[day(r['entered'])]-risk<-20-1e-8:reason='daily_including_open'
        elif cash-cost<2:reason='cash'
        if reason:blocks[reason]+=1;continue
        proceeds=5*r['exit']-r['exit_fee']-(0 if r['exit_fallback'] else 5*stress)
        net=proceeds-cost
        a=dict(r,net=net,won=net>0,reserve=reserve,proceeds=proceeds,stress=stress,model_p=0,fee=r['entry_fee']+r['exit_fee'])
        cash-=cost;active.append(a);accepted.append(a);seen.add(r['event'])
    return accepted,dict(blocks)

def summary(rows,lo,hi):
    out=summarize(rows,lo,hi)
    for k in ['mean_model_p','mean_breakeven','brier']:out.pop(k,None)
    out['win_definition']='Positive realized trade PnL after entry/exit fees and friction'
    out['gross_price_pnl']=sum(5*(r['exit']-r['entry']) for r in rows)
    out['fees']=sum(r['fee'] for r in rows)
    out['friction']=sum(5*r['stress']*(1 if r['exit_fallback'] else 2) for r in rows)
    return out

def main():
    results={};ledgers={}
    for design,intents in collect().items():
        for mode in ['base','delay60']:
            candidates=[];skips=Counter()
            for r in intents:
                f=r['future'][mode]
                if f['entry'] is None:skips['missing_entry']+=1;continue
                entry=max(r['ask'],sq(f['entry'],r['side'])[1])
                if entry>r['cap']+1e-8:skips['entry_cap']+=1;continue
                fallback=f['exit'] is None
                exit=r['outcome'] if fallback else sq(f['exit'],r['side'])[0]
                settled=r['official_settled'] if fallback else r['t']+f['exit_delay']+3
                a={k:v for k,v in r.items() if k!='future'}
                a.update(entry=entry,exit=exit,entered=r['t']+(10 if mode=='base' else 60),settled=settled,
                    exit_fallback=fallback,entry_fee=fee(entry,r['fee_multiplier']),exit_fee=0 if fallback else fee(exit,r['fee_multiplier']))
                assert r['t']<a['entered']<a['settled']
                candidates.append(a)
            for stress in [.01,.02]:
                acc,blocks=port(candidates,stress);key=f'{design}/{mode}/cost{int(stress*100)}c_each_leg'
                ledgers[key]=acc
                results[key]={'all':summary(acc,START,END),'historical':summary([r for r in acc if r['t']<SPLIT],START,SPLIT),
                    'cities':{c:metrics([r for r in acc if r['asset']==c]) for c in CITIES},
                    'intents':len(intents),'signal_events':len({r['event'] for r in intents}),
                    'entry_skips':dict(skips),'blocks':blocks,'held_to_settlement':sum(r['exit_fallback'] for r in acc)}
                if END>SPLIT:
                    results[key]['new_oct10_11']=summary([r for r in acc if r['t']>=SPLIT],SPLIT,END)
                    fresh,fresh_blocks=port([r for r in candidates if r['t']>=SPLIT],stress)
                    results[key]['new_oct10_11_fresh_cash']=summary(fresh,SPLIT,END)
                    results[key]['fresh_cash_blocks']=fresh_blocks
                print(key,{k:v for k,v in results[key]['all'].items() if k not in ('daily',)},flush=True)
    save(P/'short_results.json',results);save(P/'short_ledgers.json.gz',ledgers)
    save(P/'short_receipt.json',{'completed_ts':time.time(),'plan_sha256':hashlib.sha256((P/'SHORT_HOLD_PLAN.txt').read_bytes()).hexdigest(),
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'end':END})
if __name__=='__main__':main()
