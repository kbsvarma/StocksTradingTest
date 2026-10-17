"""Execution check of frozen model leaders and explicitly exploratory secondaries."""
import collections,gzip,json
import numpy as np
import study as s,path_model as pm,deadline_model as dm,book_replay as br
from engine import cash
P=s.P
def main():
    x,mm=s.load();books=br.load();models=json.loads((P/'path_models.json').read_text())['models'];pc=json.loads((P/'path_configs.json').read_text());dc=json.loads((P/'deadline_configs.json').read_text());targets=[('path',i) for i in (3,94,69,76)]+[('deadline',i) for i in (59,95,68,122)];out={}
    for kind,cid in targets:
        c=pc[cid] if kind=='path' else dc[cid];model=models[c['path_model']]
        if kind=='path':z=pm.transform(x,model);elig=np.ones(z.shape[:2],bool);ec=s.earray(c['entry'])
        else:z,score=dm.predict(x,model);elig=(score>=c['threshold'])|(np.arange(14,0,-1)<=c['deadline']);ec=np.array([12,14,0],float)
        for latency in (.25,3.):
            rr=[];den=collections.Counter()
            for i,m in enumerate(mm):
                bb=books.get(m['ticker']);ci=s.co(m['close'])
                if bb is None or bb[-1,0]<m['close']-35:continue
                den[ci]+=1;r=br.replay(z[i],m['result'],m['start'],m['close'],bb,ec,s.xarray(c['exit']),True,latency,elig[i]);j,side,price,k,xp,sold,trig,fallback,entered=r
                if j<0:continue
                T=m['start']+60*(j+1);debit=float(cash(price,True));credit=float(cash(xp,False)) if sold else xp;won=int(side==m['result']);xt=bb[k,0] if sold else max(m['close']+1,m['settled'])
                rr.append(dict(ticker=m['ticker'],cohort=s.PERIODS[ci],close=m['close'],entered=entered,decision=T,entry_index=int(j),side=int(side),entry=price,exit=xp,exit_ts=float(xt),sold=bool(sold),trigger_index=int(trig),armed=False,forecast_fallback=bool(fallback),debit=debit,credit=credit,legs=1+int(sold),settlement_win=won,pnl=debit+credit,hold_pnl=debit+won))
            key=f'{kind}_{cid}_delay{latency}'
            with gzip.open(P/f'extra_book_trades_{key}.json.gz','wt') as file:json.dump(rr,file,separators=(',',':'))
            periods={s.PERIODS[ci]:dict(base=s.metrics([r for r in rr if r['cohort']==s.PERIODS[ci]],den[ci],ci),cost1c=s.metrics([r for r in rr if r['cohort']==s.PERIODS[ci]],den[ci],ci,.01)) for ci in den}
            out[key]=dict(config=c,scope='frozen leader' if (kind,cid) in [('path',3),('path',94),('deadline',59),('deadline',95)] else 'post-result secondary',periods=periods)
            print(key,[(co,v['base']['trades'],v['base']['net']) for co,v in periods.items()],flush=True)
    s.save('additional_book_results.json',out)
if __name__=='__main__':main()
