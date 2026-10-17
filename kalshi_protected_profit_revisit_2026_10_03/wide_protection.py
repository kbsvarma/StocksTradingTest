"""Wide and z-conditioned protection on fixed entries, declared exploratory."""
import itertools,json,math
import numpy as np
import study as s
P=s.P
def replay(c,x,m,base):
    j=round((base['decision']-m['start'])/60)-1;direction=base['side'];armed=0;pending=None
    price=int(m['result']==direction);exit_ts=max(m['close']+30,m['settled']);reason='settlement'
    for k in range(j+1,14):
        b,a=x[k,:2]
        if np.isfinite(b+a) and not pending:
            bid=b if direction==1 else 1-a;z=x[k,6]*direction;level=(c['ideal'] if armed==2 else c['safe'])-c['buffer']
            if bid<=c['stop']+1e-8:pending='hard_stop'
            elif armed and bid<=level+1e-8:pending='safe_reversal' if armed==1 else 'ideal_reversal'
            else:
                if bid>=c['ideal']-1e-8 and (not c['z_gate'] or z>=3):armed=2
                elif bid>=c['safe']-1e-8 and (not c['z_gate'] or z>=2):armed=max(armed,1)
        if pending and np.isfinite(x[k,2:4]).all():
            b,a=x[k,2:4];v=b if direction==1 else 1-a
            if 0<v<1:price=v;exit_ts=m['start']+(k+1)*60+3;reason=pending;break
    debit=base['debit'];credit=s.cashchange(price,1,False) if reason!='settlement' else price
    return dict(base,exit=price,exit_ts=exit_ts,reason=reason,safe_armed=armed>=1,ideal_armed=armed==2,legs=1+int(reason!='settlement'),credit=credit,pnl=debit+credit)
def main():
    x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());ix={m['ticker']:i for i,m in enumerate(mm)};first=json.loads((P/'results.json').read_text());out={};selected=[]
    for cid in ('2','2462','1771','3904'):
        base=json.loads((P/f'trades_{cid}.json').read_text());keys=[]
        for stop,levels,buffer,zgate in itertools.product((.20,.30,.40),((.80,.90),(.85,.95)),(.02,.05),(False,True)):
            c=dict(entry_id=cid,stop=stop,safe=levels[0],ideal=levels[1],buffer=buffer,z_gate=zgate);key=f'{cid}_{stop}_{levels[0]}_{buffer}_{int(zgate)}';keys.append(key)
            rr=[replay(c,x[ix[r['ticker']]],mm[ix[r['ticker']]],r) for r in base]
            out[key]=dict(config=c,periods={co:dict(base=s.metrics([r for r in rr if r['cohort']==co],first[cid]['periods'][co]['base']['windows']),stress1c=s.metrics([r for r in rr if r['cohort']==co],first[cid]['periods'][co]['base']['windows'],.01)) for co in s.PERIODS[:4]})
        passed=[k for k in keys if all(out[k]['periods'][co]['base']['net']>0 for co in s.PERIODS[:2])];pick=max(passed or keys,key=lambda k:out[k]['periods']['validation']['base']['net']);selected.append(dict(entry_id=cid,passing=len(passed),pick=pick));print(cid,len(passed),pick,[(co,out[pick]['periods'][co]['base']['net'],out[pick]['periods'][co]['stress1c']['net']) for co in s.PERIODS[:4]])
        c=out[pick]['config'];s.save(f'wide_trades_{cid}.json',[replay(c,x[ix[r['ticker']]],mm[ix[r['ticker']]],r) for r in base])
    s.save('wide_results.json',dict(selected=selected,results=out))
if __name__=='__main__':main()
