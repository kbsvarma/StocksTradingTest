"""Fixed market z levels; actual entries; no live trading imports or writes."""
from pathlib import Path
import json,gzip,pickle,math,hashlib,importlib.util,bisect
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent;OLD=R/'kalshi_all_lane_exits_2026_10_14'
LEVELS=dict(BTC=-8.5,ETH=0.,SOL=-.5,XRP=-.75,COPPER=0.,NATGAS=.75,EURUSD=-2.75,WTI=-1.5,USDCAD=None)
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def sell(q,p,friction=.01):
    q=D(str(q));p=D(str(p))
    gross=q*p;fee=(D('.07')*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
    cash=(gross-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)
    return dict(price=float(p),gross=float(gross),fee=float(gross-cash),fees_only_cash=float(cash),friction=float(q*D(str(friction))),cash=float(cash-q*D(str(friction))))
def held_quote(m,end,side,suffix):
    c=m['candles'].get(int(end))
    if not c:return None
    v=c.get('b'+suffix) if side==1 else c.get('a'+{'h':'l','l':'h'}.get(suffix,suffix))
    if v is None:return None
    p=float(v) if side==1 else float(D('1')-D(str(v)))
    return p if 0<=p<=1 else None
def path(r,ss):
    tt=np.arange(math.floor(r['entered'])+1,int(r['close']),dtype=np.int64);start,v=ss[r['asset']]
    idx=tt-1-start;assert np.all((idx>=0)&(idx<len(v)))
    sp=v[idx]
    if r['asset'] in ['BTC','ETH','SOL','XRP']:
        anchor=v[int(r['t'])-1-start];assert math.isfinite(anchor) and anchor>0
        sp=r['entry_spot']*sp/anchor
    zz=r['side']*(sp/r['reference']-1)/(r['entry_sigma']*np.sqrt((r['close']-tt)/60))
    assert np.isfinite(zz).all();return tt,zz
def one(r,data,ss,threshold):
    t,z=path(r,ss);out={k:r[k] for k in ['ticker','asset','lane','won','quantity','cost','entry_fee','hold_net','entered','close','entry_z']}
    out.update(threshold=threshold,min_z=float(z.min()),source_points=len(z),breached=False,trigger=None,trigger_z=None,execution_time=None,late=False,unknown_quote=False,scenarios={})
    for name in ['low','high','stale_open_proxy']:
        out['scenarios'][name]=dict(exit=False,net=r['hold_net'],fees_only_net=r['hold_net'],delta=0.,fees_only_delta=0.,fee=0.,friction=0.,cash=0.,price=None)
    if threshold is None:return out
    ii=np.flatnonzero(z<threshold)
    if not len(ii):return out
    j=int(ii[0]);trigger=int(t[j]);at=trigger+1
    assert not (z[:j]<threshold).any()
    out.update(breached=True,trigger=trigger,trigger_z=float(z[j]),execution_time=at)
    if at>=r['close']:out['late']=True;return out
    m=data['markets'][r['ticker']];end=(at//60+1)*60
    low=held_quote(m,end,r['side'],'l');high=held_quote(m,end,r['side'],'h')
    opening=held_quote(m,(trigger//60+1)*60,r['side'],'o');previous=held_quote(m,trigger//60*60,r['side'],'c')
    out['quote_candle_end']=end
    prices=dict(low=low,high=high,stale_open_proxy=min(opening,previous) if opening is not None and previous is not None else None)
    if low is not None and high is not None:assert low<=high+1e-9
    for name,px in prices.items():
        if px is None:
            out['unknown_quote']=True;out['scenarios'][name]=dict(exit=None,net=None,fees_only_net=None,delta=None,fees_only_delta=None,fee=None,friction=None,cash=None,price=None);continue
        # A zero bid cannot substantiate an exit: retain holding in this scenario.
        if px==0:out['scenarios'][name]['no_positive_bid']=True;continue
        sold=sell(r['quantity'],px);net=sold['cash']-r['cost']-r['entry_fee'];fees_only=sold['fees_only_cash']-r['cost']-r['entry_fee']
        out['scenarios'][name]=dict(sold,exit=True,net=net,fees_only_net=fees_only,delta=net-r['hold_net'],fees_only_delta=fees_only-r['hold_net'])
    return out
def aggregate(rows,scenario):
    known=all(r['scenarios'][scenario]['net'] is not None for r in rows)
    def total(field,subset=None):
        rr=rows if subset is None else subset
        return sum(r['scenarios'][scenario][field] for r in rr) if all(r['scenarios'][scenario][field] is not None for r in rr) else None
    losses=[r for r in rows if not r['won']];wins=[r for r in rows if r['won']]
    return dict(trades=len(rows),winners=len(wins),losses=len(losses),hold=sum(r['hold_net'] for r in rows),
        threshold_crossings=sum(r['breached'] for r in rows),losses_crossing=sum(r['breached'] for r in losses),winners_crossing=sum(r['breached'] for r in wins),late=sum(r['late'] for r in rows),
        exits=sum(bool(r['scenarios'][scenario]['exit']) for r in rows),winners_cut=sum(bool(r['scenarios'][scenario]['exit']) for r in wins),
        losses_reduced=sum((r['scenarios'][scenario]['delta'] or 0)>1e-9 for r in losses),losses_fully_avoided=sum(r['scenarios'][scenario]['net'] is not None and r['scenarios'][scenario]['net']>=0 for r in losses),
        net=total('net'),fees_only_net=total('fees_only_net'),delta=total('delta'),loss_savings=total('delta',losses),fees_only_loss_savings=total('fees_only_delta',losses),winner_cost=-total('delta',wins) if total('delta',wins) is not None else None,
        exit_fees=total('fee'),friction=total('friction'),unknown_quotes=sum(r['scenarios'][scenario]['net'] is None for r in rows))
def main():
    d=pickle.load(gzip.open(P/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(P/'seconds.pkl.gz','rb'));rr=d['actual']
    rows=[one(r,d,ss,LEVELS[r['asset']]) for r in rr]
    prior={r['ticker']:r for r in json.loads((R/'kalshi_winner_z_recovery_2026_10_14/winners.json').read_text())}
    for r in rows:
        if r['won']:
            assert abs(r['min_z']-prior[r['ticker']]['primary']['minimum'])<1e-10
            assert not r['breached']
    results={name:dict(all=aggregate(rows,name),assets={a:aggregate([r for r in rows if r['asset']==a],name) for a in LEVELS},lanes={l:aggregate([r for r in rows if r['lane']==l],name) for l in ['cushion_z2','premium_z3']}) for name in ['low','high','stale_open_proxy']}
    save('ledger.json',rows);save('results.json',results);save('thresholds.json',LEVELS)
    save('usdcad_z0_diagnostic.json',[one(r,d,ss,0.) for r in rr if r['asset']=='USDCAD'])
    # Check subsecond trigger existence for crypto losses without claiming subsecond fills.
    fine=[]
    for r in rr:
        if r['won'] or r['asset'] not in ['BTC','ETH','SOL','XRP']:continue
        a=r['asset'];start,values=ss[a];anchor=values[int(r['t'])-1-start];allpoints=[]
        for h in range(int(r['entered']-1)//3600*3600,int(r['close']),3600):
            f=OLD/'cf_seconds'/f'{a}_{h}.json.gz';payload=json.load(gzip.open(f))['response']['data']['payload'];allpoints.extend((int(x['time'])/1000+1,float(x['value'])) for x in payload if r['entered']<int(x['time'])/1000+1<r['close'])
        tt=np.array([x[0] for x in allpoints]);spots=r['entry_spot']*np.array([x[1] for x in allpoints])/anchor
        z=r['side']*(spots/r['reference']-1)/(r['entry_sigma']*np.sqrt((r['close']-tt)/60));ii=np.flatnonzero(z<LEVELS[a])
        fine.append(dict(ticker=r['ticker'],minimum=float(z.min()),breached=bool(len(ii)),first_trigger=float(tt[ii[0]]) if len(ii) else None,execution_before_close=bool(len(ii) and tt[ii[0]]+1<r['close'])))
    save('finer_crypto_loss_checks.json',fine)
    # Continuous first-arrival data and full before/after depth: a separate matched subset.
    source=R/'kalshi_fast_z_exit_2026_10_14';spec=importlib.util.spec_from_file_location('prior_replay',source/'replay.py');rep=importlib.util.module_from_spec(spec);spec.loader.exec_module(rep)
    raw=pickle.load(gzip.open(source/'inputs.pkl.gz','rb'));depth=[]
    for r in raw['cohorts']['actual']:
        prepared=rep.prepare(r,raw['bundles'][r['ticker']],raw['prices'][r['asset']]);threshold=LEVELS[r['asset']]
        x=rep.simulate(prepared,1,r['entry_z']-threshold,0,0,feed='5hz',latency=1,friction=.01)
        x['threshold']=threshold
        # Reconcile a first breach versus a successfully modeled depth exit.
        crossing=[t for t,z in prepared['z'].items() if z.get('5hz',math.inf)<threshold]
        x['first_breach']=min(crossing) if crossing else None
        x['fees_only_net']=x['net']+x.get('friction',0)
        depth.append(x)
    save('depth_subset.json',dict(method='Recorded first-arrival CF5Hz; one-second checks; full held-side bid depth before and after1s; use worse proceeds. Separate from reconstructed all-history source.',metrics=rep.metrics(depth),fees_only_net=sum(r['fees_only_net'] for r in depth),ledger=depth))
    save('audit.json',dict(trades=len(rows),winners=sum(r['won'] for r in rows),losses=sum(not r['won'] for r in rows),winner_path_reproductions=len(prior),complete_source_paths=len(rows),first_breach_checks=sum(r['breached'] for r in rows),unknown_quotes=sum(r['unknown_quote'] for r in rows),thresholds_unchanged=True,no_time_filters=True,source_hashes={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [P/'inputs.pkl.gz',P/'seconds.pkl.gz',source/'inputs.pkl.gz']}))
    for name in results:print(name,json.dumps(results[name]['all']))
    print('LOSSES',json.dumps([dict(ticker=r['ticker'],level=r['threshold'],min_z=r['min_z'],breached=r['breached'],late=r['late'],low=r['scenarios']['low'],high=r['scenarios']['high']) for r in rows if not r['won']],indent=2))
    print('DEPTH',json.dumps(rep.metrics(depth)))
if __name__=='__main__':main()
