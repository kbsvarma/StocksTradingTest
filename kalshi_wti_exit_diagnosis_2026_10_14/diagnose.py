"""WTI loss-only, offline replay plus read-only live ledger reconciliation."""
import datetime as dt
import gzip
import hashlib
import json
import math
import pickle
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np

P=Path(__file__).resolve().parent
R=P.parent
sys.path.insert(0,str(R/'kalshi_latest7_exit_research_2026_10_14'))
import finalize as f
from search import firsts
ET=ZoneInfo('America/New_York')
C=dict(id='absolute_1.75_g0_p60_r0-300',family='absolute',level=1.75,grace=0,persist=60,lo=0,hi=300)
LIVE=R/'kalshi_revalidation_2026_09_12/live_v1'

def save(name,value):
    (P/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def preservation():
    names=json.loads((R/'kalshi_timed_z_exits_2026_10_14/preservation.json').read_text())['hashes']
    return dict(files={n:hashlib.sha256((LIVE/n).read_bytes()).hexdigest() for n in names},
                flags={n.name:hashlib.sha256(n.read_bytes()).hexdigest() for n in (LIVE/'state').iterdir() if n.is_file() and n.name.startswith(('ARM','HALT'))},
                services=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-resting-paper.service','-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True))

before=preservation()
db=sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON')
orders=[json.loads(x[0]) for x in db.execute("SELECT record FROM orders WHERE ticker LIKE 'KXWTI15M-%'")]
db.close()
actual=[x for x in orders if x.get('status')=='settled' and D(x.get('filled','0'))>0]
snapshot=dict(ts=time.time(),settled=len(actual),wins=sum(x['side']==x['result'] for x in actual),
              losses=sum(x['side']!=x['result'] for x in actual),net=str(sum((D(x['pnl']) for x in actual),D(0))),orders=actual)
save('live_wti_snapshot.json',snapshot)
data=pickle.load(gzip.open(f.g.PREV/'inputs.pkl.gz','rb'))
seconds=pickle.load(gzip.open(f.g.PREV/'seconds.pkl.gz','rb'))
losses=[r for r in data['actual'] if r['asset']=='WTI' and not r['won']]
assert {r['ticker'] for r in losses}=={r['ticker'] for r in actual if r['side']!=r['result']}
old_tickers={r['ticker'] for r in data['actual'] if r['asset']=='WTI'}
new=[{k:r.get(k) for k in ('ticker','lane','filled','pnl')} for r in actual if r['ticker'] not in old_tickers]
assert all(D(r['pnl'])>0 for r in new)
save('count_reconciliation.json',dict(old_wti=len(old_tickers),old_wins=len(old_tickers)-len(losses),current_settled=len(actual),current_wins=snapshot['wins'],current_losses=snapshot['losses'],new_since_frozen_replay=new))
out=[]
for r in sorted(losses,key=lambda r:r['close']):
    p=f.g.prepare(r,data,seconds)
    live=next(o for o in actual if o['ticker']==r['ticker'])
    assert r['lane']=='cushion_z2' and r['quantity']==float(live['filled'])==5
    assert abs(r['hold_net']-float(live['pnl']))<1e-9
    def point(j):
        if j is None or j<0:return None
        t=int(p['t'][j]);end=(t+1)//60*60+60
        return dict(ts=t,et=dt.datetime.fromtimestamp(t,ET).isoformat(),remaining=int(p['remaining'][j]),age=float(p['age'][j]),z=float(p['z'][j]),
                    bid_low=float(p['low']['price'][j]) if np.isfinite(p['low']['price'][j]) else None,
                    bid_high=float(p['high']['price'][j]) if np.isfinite(p['high']['price'][j]) else None,
                    execution_before_expiry=bool(p['valid'][j]),quote_minute_start=end-60,quote_minute_end=end,
                    candle=data['markets'][r['ticker']]['candles'].get(end))
    b=(p['z']<1.75)&(p['remaining']<=300)&(p['remaining']>0)
    segments=[];start=None;scalar_j=None
    for j,good in enumerate(b):
        # Independently recompute z from raw saved seconds at each check.
        sec0,prices=seconds['WTI'];spot=prices[int(p['t'][j])-1-sec0]
        zz=r['side']*(spot/r['reference']-1)/(r['entry_sigma']*math.sqrt(float(p['remaining'][j])/60))
        assert abs(zz-float(p['z'][j]))<1e-10
        if good and start is None:start=j
        if good and scalar_j is None and p['t'][j]-p['t'][start]>=60:scalar_j=j
        if start is not None and (not good or j==len(b)-1):
            endj=j if good else j-1
            segments.append(dict(start=point(start),last=point(endj),elapsed=int(p['t'][endj]-p['t'][start]),qualifies=bool(p['t'][endj]-p['t'][start]>=60),reset_at=point(j) if not good else None))
            start=None
    j=f.scalar(r,C,p)
    assert j==(-1 if scalar_j is None else scalar_j)==int(firsts(r,p,[C])[0])
    model=f.record(r,C,p,.9)
    for field,source in [('trigger',None)]:
        assert model[field]==(int(p['t'][j]) if j>=0 else None)
    first=np.flatnonzero(p['z']<1.75);window_first=np.flatnonzero(b)
    window=np.flatnonzero(p['remaining']<=300)
    diag={}
    for name,c in [('no_confirmation',dict(C,persist=0)),('no_five_minute_window',dict(C,hi=900)),('neither_wait_nor_window',dict(C,persist=0,hi=900))]:
        v=f.record(r,c,p,.9);k=f.scalar(r,c,p)
        diag[name]=dict(config=c,first_signal=point(k),modeled_exit=v['exit'],modeled_net=v['net'],saved=v['delta'])
    # Check all later eligible seconds for quote recovery; this does not grant
    # retries to the frozen first-attempt design.
    since=None;qualified=[]
    for k,good in enumerate(b):
        if not good:since=None;continue
        if since is None:since=int(p['t'][k])
        if int(p['t'][k])-since>=60:qualified.append(k)
    viable=[k for k in qualified if p['valid'][k] and p['low']['price'][k]>=.9]
    high_viable=[k for k in qualified if p['valid'][k] and p['high']['price'][k]>=.9]
    x=dict(ticker=r['ticker'],close_et=dt.datetime.fromtimestamp(r['close'],ET).isoformat(),entry_z=r['entry_z'],entry_remaining=r['close']-r['entered'],
           side='yes' if r['side']==1 else 'no',quantity=r['quantity'],hold_net=r['hold_net'],first_below_z=point(int(first[0])) if len(first) else None,
           first_eligible_breach=point(int(window_first[0])) if len(window_first) else None,window_open=point(int(window[0])) if len(window) else None,
           confirmed_signal=point(j),breach_runs=segments,exact_rule=model,diagnostics=diag,
           later_qualified_seconds_with_low_at_least90=len(viable),later_qualified_seconds_with_high_at_least90=len(high_viable),
           first_later_low90=point(viable[0]) if viable else None,first_later_high90=point(high_viable[0]) if high_viable else None)
    if j>=0:
        px=D(str(float(p['low']['price'][j])));q=D(str(r['quantity']))
        fee=(D('.07')*q*px*(1-px)).quantize(D('.000001'),rounding=ROUND_CEILING)
        proceeds=(q*px-fee).quantize(D('.0001'),rounding=ROUND_FLOOR)-D('.01')*q
        x['hypothetical_no_sell_floor']=dict(price=str(px),cash_saved=str(proceeds),net=str(proceeds-D(str(r['cost']))-D(str(r['entry_fee']))),label='Arithmetic only if the same quote supported all five; not the tested90c rule or a verified fill')
        if model['exit']:assert abs(float(x['hypothetical_no_sell_floor']['net'])-model['net'])<1e-9
    trace=[dict(ts=int(t),remaining=int(rem),z=float(z),inside_window=bool(rem<=300),below_threshold=bool(z<1.75),bid_low=float(low) if np.isfinite(low) else None,bid_high=float(high) if np.isfinite(high) else None)
           for t,rem,z,low,high in zip(p['t'],p['remaining'],p['z'],p['low']['price'],p['high']['price'])]
    with gzip.open(P/(r['ticker']+'_trace.json.gz'),'wt') as h:json.dump(trace,h)
    out.append(x)
after=preservation();assert before==after
save('loss_diagnoses.json',out)
save('audit.json',dict(captured_et=dt.datetime.now(ET).isoformat(),exact_rule=C,sell_limit=.9,losses=len(out),scalar_vector_matches=5,
     price_path_checks=sum(len(f.g.prepare(r,data,seconds)['t']) for r in losses),all_losses_triggered=all(x['confirmed_signal'] for x in out),
     blocked_by90c=sum(not x['exact_rule']['exit'] for x in out),live_unchanged=True,preservation_before=before,preservation_after=after,
     input_hashes={str(v):hashlib.sha256(v.read_bytes()).hexdigest() for v in [f.g.PREV/'inputs.pkl.gz',f.g.PREV/'seconds.pkl.gz']}))
for x in out:
    print(json.dumps({k:x[k] for k in ['ticker','entry_z','first_below_z','first_eligible_breach','confirmed_signal','diagnostics','later_qualified_seconds_with_low_at_least90','later_qualified_seconds_with_high_at_least90','hypothetical_no_sell_floor']},indent=2))
