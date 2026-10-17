"""Isolated WTI fixed-entry exit study. Public GETs and read-only preservation."""
import datetime as dt
import gzip
import hashlib
import json
import math
import pickle
import subprocess
import sys
import time
from collections import Counter
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import requests

P=Path(__file__).resolve().parent; R=P.parent
OLD=R/'kalshi_wti_exit_diagnosis_2026_10_14'
PREV=R/'kalshi_market_z_stops_2026_10_14'
LIVE=R/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(R/'kalshi_latest7_exit_research_2026_10_14'))
import finalize as f
from search import firsts
ET=ZoneInfo('America/New_York')
C=dict(id='absolute_1.75_g0_p60_r0-300',family='absolute',level=1.75,grace=0,persist=60,lo=0,hi=300)

def save(n,x): (P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def preservation():
    old=json.loads((OLD/'audit.json').read_text())['preservation_after']
    return dict(files={n:digest(LIVE/n) for n in old['files']},
      flags={p.name:digest(p) for p in (LIVE/'state').iterdir() if p.is_file() and p.name.startswith(('ARM','HALT'))},
      services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True))

def prepare_inputs():
    snap=json.loads((OLD/'live_wti_snapshot.json').read_text());orders=snap['orders']
    assert (len(orders),sum(o['side']==o['result'] for o in orders))==(111,106)
    assert sum((D(o['pnl']) for o in orders),D(0))==D('15.3050')
    save('frozen_orders.json',snap)
    raw=pickle.load(gzip.open(PREV/'inputs.pkl.gz','rb'))
    ss0=pickle.load(gzip.open(PREV/'seconds.pkl.gz','rb'));ss={'WTI':ss0['WTI']}
    known={r['ticker']:r for r in raw['actual']};data=dict(actual=[],markets={})
    session=requests.Session();(P/'fresh').mkdir(exist_ok=True)
    def get(name,endpoint,params=None):
        dest=P/'fresh'/name
        if not dest.exists():
            started=time.time();res=session.get('https://external-api.kalshi.com/trade-api/v2'+endpoint,params=params,timeout=20);res.raise_for_status()
            dest.write_text(json.dumps(dict(started=started,received=time.time(),url=res.url,response=res.json()))+'\n')
        return json.loads(dest.read_text())['response']
    series=get('WTI_series.json','/series/KXWTI15M')['series']
    assert series['fee_type']=='quadratic' and float(series['fee_multiplier'])==1
    for o in sorted(orders,key=lambda o:o['close_ts']):
        t=o['ticker'];s=o['signal']
        if t in known:
            r=known[t];data['markets'][t]=raw['markets'][t]
        else:
            m=get(t+'_market.json','/markets/'+t)['market'];assert m['result']==o['result']
            cc=get(t+'_candles.json','/series/KXWTI15M/markets/'+t+'/candlesticks',dict(start_ts=o['close_ts']-900,end_ts=o['close_ts'],period_interval=1))['candlesticks']
            candles={v['end_period_ts']:{'t':v['end_period_ts'],**{x+y:v.get(n,{}).get(k+'_dollars') for x,n in [('b','yes_bid'),('a','yes_ask')] for y,k in [('o','open'),('c','close'),('h','high'),('l','low')]}} for v in cc}
            settled=dt.datetime.fromisoformat(m['settlement_ts'].replace('Z','+00:00')).timestamp()
            data['markets'][t]=dict(asset='WTI',result=1 if o['result']=='yes' else -1,candles=candles,settled=settled)
            r=dict(ticker=t,asset='WTI',lane=o['lane'],t=s['decision_ts'],entered=s.get('execution_ack_ts',o['created_ts']),close=o['close_ts'],settled=settled,side=1 if o['side']=='yes' else -1,quantity=float(o['filled']),cost=float(o['cost']),entry_fee=float(o['fees']),entry_friction=0,hold_net=float(o['pnl']),won=int(o['side']==o['result']),entry_z=s['z'],entry_sigma=s['sigma'],reference=s['strike_proxy'],entry_spot=s['spot'],entry_gate=s['required_z'],ranges=m['price_ranges'])
            chart=get(m['event_ticker']+'_chart.json','/live_data/events/'+m['event_ticker'],dict(range='1h'))['live_data']['details'];assert chart['asset']=='WTI'
            start,values=ss['WTI'];arr=chart['timeseries'];need=max(int(q['t'])//1000 for q in arr)+1-start
            if need>len(values):values=np.pad(values,(0,need-len(values)),constant_values=np.nan)
            for q in arr:
                at=int(q['t'])//1000
                if at>=start:
                    # Existing cached historical prices must never be revised.
                    prior=values[at-start]
                    if np.isfinite(prior):assert abs(prior-float(q['v']))<1e-9
                    values[at-start]=float(q['v'])
            ss['WTI']=(start,values)
        assert abs(r['hold_net']-float(o['pnl']))<1e-9
        assert r['quantity']==float(o['filled'])==5
        assert D(o['filled'])*int(r['won'])-D(o['cost'])-D(o['fees'])==D(o['pnl'])
        data['actual'].append(r)
    for name,obj in [('inputs.pkl.gz',data),('seconds.pkl.gz',ss)]:
        with gzip.open(P/name,'wb') as h:pickle.dump(obj,h)
    return data,ss

def calc(r,p,j,floor,scope,friction=.01):
    x={k:r[k] for k in ['ticker','lane','won','quantity','cost','entry_fee','hold_net','close','entered']}
    x.update(floor=floor,scope=scope,trigger=int(p['t'][j]) if j>=0 else None,trigger_z=float(p['z'][j]) if j>=0 else None,
      remaining=int(p['remaining'][j]) if j>=0 else None,bid_low=float(p['low']['price'][j]) if j>=0 and np.isfinite(p['low']['price'][j]) else None,
      bid_high=float(p['high']['price'][j]) if j>=0 and np.isfinite(p['high']['price'][j]) else None,
      exit=False,net=r['hold_net'],fees_only_net=r['hold_net'],exit_fee=0.,friction=0.,reason='no_confirmed_signal')
    if scope=='original_only' and r['lane']!='cushion_z2':x['reason']='premium_held_unchanged'
    elif j<0:pass
    elif not p['valid'][j]:x['reason']='too_late'
    elif not p['low']['fill'][j]:x['reason']='no_positive_bid'
    elif D(str(x['bid_low']))<D(str(floor)):x['reason']='below_floor'
    else:
        q=D(str(r['quantity']));px=D(str(x['bid_low']));fee=(D('.07')*q*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
        cash=(q*px-fee).quantize(D('.0001'),rounding=ROUND_FLOOR);fr=q*D(str(friction))
        net=cash-D(str(r['cost']))-D(str(r['entry_fee']))
        x.update(exit=True,net=float(net-fr),fees_only_net=float(net),exit_fee=float(q*px-cash),friction=float(fr),reason='modeled_exit')
        assert abs(float(p['low']['net'][j])-(x['net']+(friction-.01)*r['quantity']))<1e-9
    x.update(delta=x['net']-x['hold_net'],fees_only_delta=x['fees_only_net']-x['hold_net'])
    return x

def metrics(xs):
    losses=[x for x in xs if not x['won']];wins=[x for x in xs if x['won']];cuts=[x for x in wins if x['exit']]
    out=dict(trades=len(xs),wins=len(wins),losses=len(losses),hold_net=sum(x['hold_net'] for x in xs),exits=sum(x['exit'] for x in xs),
      losses_reduced=sum(x['delta']>0 for x in losses),losses_saved=sum(x['delta'] for x in losses),fees_only_losses_saved=sum(x['fees_only_delta'] for x in losses),
      winners_cut=len(cuts),winners_turned_loss=sum(x['net']<0 for x in cuts),winner_cost=-sum(x['delta'] for x in wins),fees_only_winner_cost=-sum(x['fees_only_delta'] for x in wins),
      cut_winners_original_profit=sum(x['hold_net'] for x in cuts),cut_winners_exit_net=sum(x['net'] for x in cuts),
      net=sum(x['net'] for x in xs),fees_only_net=sum(x['fees_only_net'] for x in xs),delta=sum(x['delta'] for x in xs),
      exit_fees=sum(x['exit_fee'] for x in xs),friction=sum(x['friction'] for x in xs),entry_fees=sum(x['entry_fee'] for x in xs),
      reasons=dict(Counter(x['reason'] for x in xs)))
    cumulative=np.cumsum([x['net'] for x in sorted(xs,key=lambda x:x['close'])]);peaks=np.maximum.accumulate(np.r_[0,cumulative])[1:]
    out['max_drawdown']=float(max(peaks-cumulative)) if xs else 0.
    assert abs(out['net']-out['hold_net']-out['losses_saved']+out['winner_cost'])<1e-8
    assert abs(out['net']+out['friction']-out['fees_only_net'])<1e-8
    return out

def main():
    before=preservation();save('preservation_before.json',before)
    data,ss=prepare_inputs();rows=data['actual'];paths={};triggers={};checks=0
    for r in rows:
        p=f.g.prepare(r,data,ss);paths[r['ticker']]=p
        j=f.scalar(r,C,p);assert j==int(firsts(r,p,[C])[0]);triggers[r['ticker']]=j
        start,values=ss['WTI']
        for t,z in zip(p['t'],p['z']):
            zz=r['side']*((values[int(t)-1-start]-r['reference'])/r['reference'])/(r['entry_sigma']*math.sqrt((r['close']-int(t))/60))
            assert abs(zz-z)<1e-9;checks+=1
    old=json.loads((OLD/'loss_diagnoses.json').read_text())
    for x in old:
        p=paths[x['ticker']];j=triggers[x['ticker']]
        assert int(p['t'][j])==x['confirmed_signal']['ts']
        assert abs(float(p['low']['price'][j])-x['confirmed_signal']['bid_low'])<1e-9
    ledgers={};summaries={};sens={}
    for scope in ['original_only','all_wti']:
        last_count=-1
        for cents in range(90,-1,-1):
            floor=cents/100;key=f'{scope}_{cents:02d}'
            xs=[calc(r,paths[r['ticker']],triggers[r['ticker']],floor,scope) for r in rows]
            m=metrics(xs);assert m['exits']>=last_count;last_count=m['exits']
            ledgers[key]=xs;summaries[key]=m
        for cents in [90,85,80,75,70,65,60,50,40,30,20,10,0]:
            key=f'{scope}_{cents:02d}';floor=cents/100
            stress2=[calc(r,paths[r['ticker']],triggers[r['ticker']],floor,scope,.02) for r in rows]
            p3={r['ticker']:f.g.prepare(r,data,ss,latency=3) for r in rows}
            stress3=[calc(r,p3[r['ticker']],triggers[r['ticker']],floor,scope) for r in rows]
            sens[key]=dict(friction2c=metrics(stress2),order_latency3s=metrics(stress3))
    save('results.json',summaries);save('sensitivity.json',sens)
    with gzip.open(P/'ledgers.json.gz','wt') as h:json.dump(ledgers,h)
    records=[calc(r,paths[r['ticker']],triggers[r['ticker']],0,'all_wti') for r in rows]
    save('all_trigger_details.json',records)
    after=preservation();assert before==after;save('preservation_after.json',after)
    audit=dict(captured_et=dt.datetime.now(ET).isoformat(),cohort=metrics([dict(x,exit=False,net=x['hold_net'],fees_only_net=x['hold_net'],delta=0.,fees_only_delta=0.,exit_fee=0.,friction=0.) for x in records]),
      lane_counts={str(k):v for k,v in Counter((r['lane'],r['won']) for r in rows).items()},rule=C,scopes=['original_only','all_wti'],floors=91,
      scalar_vector_checks=len(rows),independent_z_checks=checks,prior_loss_triggers_matched=5,live_unchanged=True,
      first_close_et=dt.datetime.fromtimestamp(min(r['close'] for r in rows),ET).isoformat(),last_close_et=dt.datetime.fromtimestamp(max(r['close'] for r in rows),ET).isoformat(),
      hashes={str(p):digest(p) for p in [OLD/'live_wti_snapshot.json',PREV/'inputs.pkl.gz',PREV/'seconds.pkl.gz',P/'inputs.pkl.gz',P/'seconds.pkl.gz',P/'PLAN.txt',P/'replay.py']},
      receipt_hashes={p.name:digest(p) for p in (P/'fresh').iterdir()})
    save('audit.json',audit)
    for scope in ['original_only','all_wti']:
        for c in [90,85,80,75,70,65,60,50,40,30,20,10,0]:
            print(scope,c,json.dumps(summaries[f'{scope}_{c:02d}']),flush=True)
    print('AUDIT',json.dumps({k:audit[k] for k in ['lane_counts','scalar_vector_checks','independent_z_checks','live_unchanged','first_close_et','last_close_et']}),flush=True)

if __name__=='__main__':main()
