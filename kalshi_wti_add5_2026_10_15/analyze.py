"""Offline additional5-contract arithmetic and chronological public-tape proxy."""
import bisect
import datetime as dt
import gzip
import hashlib
import json
import math
import pickle
from collections import Counter
from decimal import Decimal as D,ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from capture import ask
P=Path(__file__).resolve().parent;BASE=P.parent/'kalshi_wti_sell_floors_2026_10_15';ET=ZoneInfo('America/New_York')
CAPS=[20,30,40,50];SPLIT=dt.datetime.fromisoformat('2026-10-12T00:00:00-04:00').timestamp()
ZF=[('any',None,None)]+[(f'z_ge_{z:g}',z,None) for z in [-2,-1,-.5,0,.5,1,1.5,2]]+[('z_lt_0',None,0),('z_0_to_1',0,1),('z_1_to_2',1,2)]
#12 filters:1unrestricted+8lower bounds+3disjoint ranges; plan arithmetic corrected before replay.
WINDOWS=[('all',0,900),('gt60',60,900),('gt120',120,900),('gt180',180,900),('last60',0,60),('60to180',60,180),('180to300',180,300),('300to900',300,900)]
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def cash(cap):
    p=D(cap)/100;fee=(D('.07')*5*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
    total=(5*p+fee).quantize(D('.0001'),rounding=ROUND_CEILING)
    return dict(price=float(p),quantity=5,cost=float(5*p),fee=float(total-5*p),cash_spent=float(total))
def row(r,cap):
    return dict(ticker=r['ticker'],lane=r['lane'],won=bool(r['won']),hold_net=r['hold_net'],close=r['close'],settled=r['settled'],entered=r['entered'],cap=cap,added=False,extra_net=0.,friction=0.,extra_net_friction=0.,cash_spent=0.,fee=0.,cost=0.)
def add(x,**details):
    c=cash(x['cap']);net=5*int(x['won'])-c['cash_spent']
    x.update(c,added=True,extra_net=net,friction=.05,extra_net_friction=net-.05,**details)
    return x
def metrics(xs):
    win=[x for x in xs if x['added'] and x['won']];loss=[x for x in xs if x['added'] and not x['won']]
    hold=sum(x['hold_net'] for x in xs);extra=sum(x['extra_net'] for x in xs)
    out=dict(trades=len(xs),original_wins=sum(x['won'] for x in xs),adds=len(win)+len(loss),add_wins=len(win),add_losses=len(loss),
      extra_winner_profit=sum(x['extra_net'] for x in win),extra_losses=-sum(x['extra_net'] for x in loss),extra_net=extra,
      hold_net=hold,total_net=hold+extra,total_net_friction=hold+sum(x['extra_net_friction'] for x in xs),
      fees=sum(x['fee'] for x in xs),friction=sum(x['friction'] for x in xs),cash_turnover=sum(x['cash_spent'] for x in xs))
    assert abs(out['extra_winner_profit']-out['extra_losses']-extra)<1e-8
    return out
def split(xs):
    return dict(full=metrics(xs),development=metrics([x for x in xs if x['close']<SPLIT]),later=metrics([x for x in xs if x['close']>=SPLIT]))
def qualified(z,remaining,zfilter,window):
    _,low,high=zfilter;_,lo,hi=window
    return np.isfinite(z)&(remaining>lo)&(remaining<=hi)&((z>=low) if low is not None else True)&((z<high) if high is not None else True)
def z_at(r,times,ss):
    start,values=ss['WTI'];times=np.asarray(times,dtype=float)
    ix=np.floor(times-1).astype(np.int64)-start;assert ((ix>=0)&(ix<len(values))).all()
    spots=values[ix];remaining=r['close']-times
    z=r['side']*(spots/r['reference']-1)/(r['entry_sigma']*np.sqrt(np.maximum(remaining,.000001)/60))
    assert np.isfinite(z).all()
    return z,remaining
def make_path(r,trades,ss,latency):
    side='yes' if r['side']==1 else 'no';xs=[];bad=0
    for t in trades:
        stamp=t['created_time'].replace('Z','+00:00')
        ts=(dt.datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%S.%f%z') if '.' in stamp else dt.datetime.fromisoformat(stamp)).timestamp()
        if not r['entered']<ts<r['close']:continue
        # Legacy taker_side is the bought outcome. New book_side uses a different
        # coordinate convention (yes->bid,no->ask here), not an action filter.
        if t.get('taker_side') not in ['yes','no']:bad+=1;continue
        if t.get('taker_side')!=side:continue
        assert not t.get('is_block_trade',False)
        yes=D(t['yes_price_dollars']);no=D(t['no_price_dollars']);assert yes+no==1
        xs.append((ts,float(D(t[side+'_price_dollars'])),float(D(t['count_fp'])),t['trade_id']))
    xs.sort(key=lambda x:(x[0],x[3]));tt=np.array([x[0] for x in xs]);px=np.array([x[1] for x in xs]);qq=np.array([x[2] for x in xs])
    # Signal first arrives1s after trade. Revalidate one latency interval before
    # execution: source value itself has assumed1s availability lag.
    signal_t=tt+1;sz,sr=z_at(r,signal_t,ss) if len(tt) else (np.array([]),np.array([]))
    fz,fr=z_at(r,tt-latency,ss) if len(tt) else (np.array([]),np.array([]))
    iz,ir=z_at(r,tt,ss) if len(tt) else (np.array([]),np.array([]))
    return dict(t=tt,p=px,q=qq,ids=[x[3] for x in xs],signal_t=signal_t,signal_z=sz,signal_remaining=sr,fill_z=fz,fill_remaining=fr,instant_z=iz,instant_remaining=ir,unknown_aggressors=bad)
def simulate(r,cap,zf,w,path,latency=1,instant=False):
    x=row(r,cap);n=len(path['t']);x['signals']=0;x['reason']='no_qualifying_buy_print'
    if not n:return x
    sig=qualified(path['signal_z'],path['signal_remaining'],zf,w)&(path['p']<=cap/100)&(path['signal_t']<r['close'])
    # Fill itself must be within time window, not just the earlier recheck.
    fill=qualified(path['fill_z'],path['fill_remaining'],zf,w)&(path['p']<=cap/100)&(path['q']>=5)&(r['close']-path['t']>w[1])&(r['close']-path['t']<=w[2])
    if instant:
        instant_ok=qualified(path['instant_z'],path['instant_remaining'],zf,w)&(path['p']<=cap/100)&(path['q']>=5)
        candidates=np.flatnonzero(instant_ok)
        if not len(candidates):return x
        k=int(candidates[0]);return add(x,fill_time=float(path['t'][k]),signal_time=float(path['t'][k]),execution_trade=path['ids'][k],execution_print_price=float(path['p'][k]),execution_print_qty=float(path['q'][k]),signal_z=float(path['instant_z'][k]),fill_z=float(path['instant_z'][k]),remaining=float(r['close']-path['t'][k]),reason='instant_print_diagnostic')
    fi=np.flatnonzero(fill);fts=path['t'][fi]
    for j in np.flatnonzero(sig):
        x['signals']+=1;decision=float(path['signal_t'][j]);earliest=decision+latency
        k=int(np.searchsorted(fts,earliest,side='left'))
        if k>=len(fi) or fts[k]>earliest+5:continue
        k=int(fi[k]);assert path['ids'][k]!=path['ids'][j]
        return add(x,signal_time=decision,signal_print_time=float(path['t'][j]),signal_trade=path['ids'][j],fill_time=float(path['t'][k]),execution_trade=path['ids'][k],execution_print_price=float(path['p'][k]),execution_print_qty=float(path['q'][k]),signal_z=float(path['signal_z'][j]),fill_z=float(path['fill_z'][k]),remaining=float(r['close']-path['t'][k]),reason='subsequent_buy_print_proxy')
    if x['signals']:x['reason']='no_subsequent_five_contract_print_within_deadline'
    return x
def risk(xs):
    events=[];whole=[]
    for x in xs:
        whole.extend([(x['entered'],5),(x['settled'],-5)])
        if x['added']:
            events.extend([(x['fill_time'],1,x['cash_spent'],5,0),(x['settled'],0,-x['cash_spent'],-5,5*int(x['won']))])
            whole.extend([(x['fill_time'],5),(x['settled'],-5)])
    active=0.;contracts=0;cash_balance=0.;peak=0.;peakcontracts=0;bankroll=0.
    for t,kind,amount,q,payout in sorted(events):
        active+=amount;contracts+=q
        if kind==1:cash_balance-=amount
        else:cash_balance+=payout
        peak=max(peak,active);peakcontracts=max(peakcontracts,contracts);bankroll=max(bankroll,-cash_balance)
    current=0;whole_peak=0
    for t,q in sorted(whole):current+=q;whole_peak=max(whole_peak,current)
    return dict(peak_added_cash_committed=peak,peak_added_contracts=peakcontracts,peak_whole_wti_contracts=whole_peak,incremental_bankroll_to_fund_all_adds=bankroll)
def bootstrap(xs):
    days=[dt.date(2026,10,8)+dt.timedelta(days=i) for i in range(7)]
    vals=np.array([sum(x['extra_net_friction'] for x in xs if dt.datetime.fromtimestamp(x['close'],ET).date()==day) for day in days]);rng=np.random.default_rng(20261015)
    draws=vals[rng.integers(0,7,(10000,7))].sum(axis=1)
    return dict(daily_extra_net_friction={str(d):float(v) for d,v in zip(days,vals)},bootstrap95_total_extra_friction=[float(v) for v in np.quantile(draws,[.025,.975])],selection_adjusted=False)
def main():
    data=pickle.load(gzip.open(BASE/'inputs.pkl.gz','rb'));rows=data['actual'];ss=pickle.load(gzip.open(BASE/'seconds.pkl.gz','rb'));trades=json.load(gzip.open(P/'trades.json.gz'))
    assert len(rows)==111 and sum(r['won'] for r in rows)==106
    touches={};touchrows={}
    for cap in CAPS:
        xs=[]
        for r in rows:
            x=row(r,cap);quotes=[];amb=[]
            for end,c in sorted(data['markets'][r['ticker']]['candles'].items()):
                if not r['entered']<end<=r['close']:continue
                if end-60>=r['entered']:quotes.append((end-60,end,ask(c,r['side'],'l')))
                else:
                    if end<r['close']:quotes.append((end,end,ask(c,r['side'],'c')))
                    amb.append((end-60,end,ask(c,r['side'],'l')))
            qualify=[v for v in quotes if v[2] is not None and 0<v[2]<=D(cap)/100]
            if qualify:
                start,end,px=qualify[0];add(x,interval_start=start,interval_end=end,ask_low=float(px),reason='ask_touch_assumed_cap_fill')
            x['ambiguous_entry_minute_only']=not bool(qualify) and any(v[2] is not None and 0<v[2]<=D(cap)/100 for v in amb)
            xs.append(x)
        touches[str(cap)]=split(xs);touchrows[str(cap)]=xs
    save('price_touch_results.json',touches);save('price_touch_ledgers.json',touchrows)
    paths={r['ticker']:make_path(r,trades.get(r['ticker'],[]),ss,1) for r in rows}
    paths3={r['ticker']:make_path(r,trades.get(r['ticker'],[]),ss,3) for r in rows}
    configs=[];grid={};ledgers={}
    for cap in CAPS:
        for zf in ZF:
            for w in WINDOWS:
                key=f'cap{cap}_{zf[0]}_{w[0]}';config=dict(id=key,cap=cap,zfilter=zf,window=w);configs.append(config)
                xs=[simulate(r,cap,zf,w,paths[r['ticker']]) for r in rows];ledgers[key]=xs;grid[key]=split(xs)
    save('configs.json',configs);save('grid_results.json',grid)
    with gzip.open(P/'grid_ledgers.json.gz','wt') as h:json.dump(ledgers,h)
    selected={};sens={};byid={c['id']:c for c in configs}
    for cap in CAPS:
        keys=[c['id'] for c in configs if c['cap']==cap]
        dev=[k for k in keys if grid[k]['development']['adds']>=5]
        chosen=max(dev,key=lambda k:(grid[k]['development']['extra_net']-grid[k]['development']['friction'],-len(k),k)) if dev else None
        best=max(keys,key=lambda k:(grid[k]['full']['extra_net']-grid[k]['full']['friction'],-len(k),k))
        selected[str(cap)]=dict(development_choice=chosen,development_eligible_variants=len(dev),development_choice_beats_hold=grid[chosen]['development']['extra_net']-grid[chosen]['development']['friction']>0 if chosen else None,full_hindsight_choice=best)
    displays={f'cap{c}_any_all' for c in CAPS}
    displays.update(v[k] for v in selected.values() for k in ['development_choice','full_hindsight_choice'] if v[k])
    for cap in CAPS:
        displays.update(f'cap{cap}_{z}_{w}' for z,w in [('z_ge_0','all'),('z_ge_0','gt60'),('z_ge_-0.5','gt60'),('any','gt60'),('any','gt120'),('z_ge_0','last60')])
    for key in sorted(displays):
        c=byid[key];xs=ledgers[key]
        late=[simulate(r,c['cap'],c['zfilter'],c['window'],paths3[r['ticker']],latency=3) for r in rows]
        instant=[simulate(r,c['cap'],c['zfilter'],c['window'],paths[r['ticker']],instant=True) for r in rows]
        sens[key]=dict(config=c,summary=grid[key],latency3s=split(late),instant_print=split(instant),friction2c_total=grid[key]['full']['total_net']-.10*grid[key]['full']['adds'],risk=risk(xs),uncertainty=bootstrap(xs))
    save('selected.json',selected);save('selected_sensitivity.json',sens)
    save('selected_ledgers.json',{k:ledgers[k] for k in sorted(displays)})
    save('analysis_manifest.json',dict(configurations=len(configs),zfilters=len(ZF),time_windows=len(WINDOWS),cohort=111,unknown_aggressor_records=sum(p['unknown_aggressors'] for p in paths.values()),
      input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/'inputs.pkl.gz',BASE/'seconds.pkl.gz',P/'trades.json.gz',P/'PLAN.txt',P/'analyze.py']}))
    for cap in CAPS:print('TOUCH',cap,json.dumps(touches[str(cap)]['full']))
    for cap in CAPS:print('TAPE',cap,json.dumps(grid[f'cap{cap}_any_all']['full']))
    for cap,v in selected.items():
        for typ in ['development_choice','full_hindsight_choice']:print('SELECTED',cap,typ,v[typ],json.dumps(grid[v[typ]]) if v[typ] else 'insufficient development additions')
    for key in sorted(displays):
        if key.endswith('_gt60') or key.endswith('_last60'):print('FILTER',key,json.dumps(grid[key]['full']))
if __name__=='__main__':main()
