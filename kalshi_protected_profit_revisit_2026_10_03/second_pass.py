"""Post-result protected-exit diagnostic. Fixed entries; no parameter search."""
import collections,json,math
import numpy as np
import study as s
from state_machine import Protection
P=s.P
def replay(c,variant,x,m,base):
    j=round((base['decision']-m['start'])/60)-1;direction=base['side'];entry=base['entry'];state=Protection.from_config(entry,14-j,c)
    previous=None;consecutive=0;pending=None;armed=0;peak=entry
    entry_underlying=x[j,6]*x[j,9]*math.sqrt(14-j)
    price=int(m['result']==direction);exit_ts=max(m['close']+30,m['settled']);reason='settlement'
    for k in range(j+1,14):
        b,a=x[k,:2]
        if np.isfinite(b+a) and not pending:
            bid=b if direction==1 else 1-a;level=(state.ideal if armed==2 else state.safe)-state.buffer
            if c['mode']==4 and armed==2:level=max(level,peak-.06)
            if bid<=state.stop+1e-8:pending='hard_stop'
            else:
                hit=bool(armed and bid<=level+1e-8);consecutive=consecutive+1 if hit else 0
                if variant=='downward':confirm=previous is not None and bid<previous-1e-8
                elif variant=='two_quotes':confirm=consecutive>=2
                elif variant=='underlying_reversal':confirm=direction*(x[k,6]*x[k,9]*math.sqrt(14-k)-entry_underlying)<0
                else:
                    p=x[k,8] if direction==1 else 1-x[k,8];confirm=p<s.cashchange(bid,1,False)
                if hit and confirm:pending='safe_reversal' if armed==1 else 'ideal_reversal'
                else:
                    peak=max(peak,bid)
                    if bid>=state.ideal-1e-8:armed=2
                    elif bid>=state.safe-1e-8:armed=max(armed,1)
            previous=bid
        if pending and np.isfinite(x[k,2:4]).all():
            b,a=x[k,2:4];v=b if direction==1 else 1-a
            if 0<v<1:price=v;exit_ts=m['start']+(k+1)*60+3;reason=pending;break
    debit=base['debit'];credit=s.cashchange(price,1,False) if reason!='settlement' else price
    return dict(base,exit=price,exit_ts=exit_ts,reason=reason,safe_armed=armed>=1,ideal_armed=armed==2,legs=1+int(reason!='settlement'),credit=credit,pnl=debit+credit)

def main():
    x=np.load(P/'market_arrays.npy');mm=json.loads((P/'markets.json').read_text());ix={m['ticker']:i for i,m in enumerate(mm)}
    first=json.loads((P/'results.json').read_text());allstats={};selection=[]
    for cid,result in first.items():
        c=result['config'];base=json.loads((P/f'trades_{cid}.json').read_text())
        for variant in ('downward','two_quotes','underlying_reversal','economic_protection'):
            rr=[replay(c,variant,x[ix[r['ticker']]],mm[ix[r['ticker']]],r) for r in base]
            key=cid+'_'+variant;s.save(f'second_pass_trades_{key}.json',rr)
            allstats[key]={co:s.metrics([r for r in rr if r['cohort']==co],result['periods'][co]['base']['windows']) for co in s.PERIODS[:4]}
    for asset in ('BTC','ETH'):
        keys=[k for k in allstats if first[k.split('_')[0]]['config']['asset']==asset];passed=[k for k in keys if all(allstats[k][co]['net']>0 and allstats[k][co]['trades']>=200 for co in s.PERIODS[:2])]
        best=max(passed or keys,key=lambda k:allstats[k]['validation']['net']);selection.append(dict(asset=asset,passing=len(passed),best=best,stats=allstats[best]));print(asset,len(passed),best,[(co,allstats[best][co]['net']) for co in s.PERIODS[:4]])
    s.save('second_pass_results.json',dict(selection=selection,results=allstats,note='Exploratory follow-up declared after first-pass later results were known.'))
if __name__=='__main__':main()
