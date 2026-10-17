"""Run observation by default. Live needs --live and ARM_5CT; new z3 entries also need ARM_Z3_5CT."""
from __future__ import annotations
import argparse
from decimal import Decimal
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import time
import yaml
from sizing import CONTRACTS, MAX_CONTRACTS, PREFLIGHT_CASH, ARM_FILE
from engine import Engine,Exchange,Store,ACTIVE,MAX_RISK,DAILY_LOSS_BUDGET
from kalshi_client import Credentials,KalshiClientV2
import market_data
from execution_retry import retry_allowed
from lanes import LANES, Z2, Z3, lane_for, z3_permission, Z3_MAX_CONTRACTS, Z3_MAX_RISK, Z3_DAILY_BUDGET

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state'
LEGACY=Path('/home/varma/kalshi/kalshi_unbroken_bot')
LOG=logging.getLogger('crypto-z2')

def atomic(path,data):
    tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
    tmp.replace(path)

def legacy_safe():
    if not (LEGACY/'cushion/state/HALT_CUSHION').exists():return 'Legacy cushion must remain halted'
    cfg=yaml.safe_load((LEGACY/'config.yaml').read_text())
    if cfg.get('r1_enabled',True) or any((cfg.get(k) or {}).get('enabled',False) for k in ('lock_lane','stab_lane')):
        return 'Legacy crypto lanes must remain disabled'
    return ''

def live_permission():
    try:
        if not (STATE/ARM_FILE).exists() or (STATE/ARM_FILE).read_text().strip()!=str(CONTRACTS):return 'Live arm removed or invalid'
        return legacy_safe()
    except Exception as e:return 'Activation check failed: '+str(e)[:150]

def display_signal(rec,mode):
    rec=dict(rec)
    rec['mode']=mode
    rec['status']={'paper_fill':'eligible','already_paper_entered':'already_entered'}.get(rec.get('status'),rec.get('status'))
    rec.pop('note',None)
    return rec

def status(engine,scans,mode,error='',stopped=False):
    rows=engine.store.all()
    for row in rows:
        if row.get('signal'):row['signal']=display_signal(row['signal'],row['signal'].get('mode',mode))
    pending=any(r['status'] in ('intent','unknown','ack','accounting_pending') for r in rows)
    return dict(ts=time.time(),mode=mode,stopped=stopped,
        strategy='crypto_z2_and_z3',lanes=lane_status(engine,rows,mode),contracts=CONTRACTS,max_contracts=int(MAX_CONTRACTS),max_risk=str(MAX_RISK),
        daily_loss_budget=str(DAILY_LOSS_BUDGET),band='85–<95¢ signal; max 93.9¢ buy',signal_ask_ceiling_dollars='0.95',execution_cap_dollars='0.939',z_min=2,minutes='3–10',
        execution='Capped IOC; one attempt per market; hold to settlement',
        armed=(STATE/ARM_FILE).exists(),halted=engine.halt_reason() or ('Order reconciliation pending' if pending else ''),error=error,
        account=engine.last_account,scans=[display_signal(r,mode) for r in scans.values()],
        active=[r for r in rows if r['status'] in ACTIVE],
        realized_pnl=sum(float(r.get('pnl') or 0) for r in rows),
        settled=sum(r['status']=='settled' for r in rows),attempts=len(rows),
        trades=rows[-100:])

def lane_status(engine,rows,mode):
    out={}
    for name,rule in LANES.items():
        own=[r for r in rows if lane_for(r).name==name]
        settled=[r for r in own if r['status']=='settled']
        block=z3_permission(STATE) if name==Z3 else ''
        out[name]=dict(contracts=rule.contracts,z_min=rule.z,signal_floor=str(rule.floor),
            signal_ceiling=str(rule.ceiling),execution_cap=str(rule.cap),
            enabled=mode=='LIVE' and not block and not engine.halt_reason(),blocked=block,
            max_contracts=int(Z3_MAX_CONTRACTS) if name==Z3 else int(MAX_CONTRACTS),
            max_risk=str(Z3_MAX_RISK) if name==Z3 else str(MAX_RISK),
            daily_loss_budget=str(Z3_DAILY_BUDGET) if name==Z3 else str(DAILY_LOSS_BUDGET),
            attempts=len(own),settled=len(settled),
            wins=sum(r.get('side')==r.get('result') for r in settled),
            losses=sum(r.get('side')!=r.get('result') for r in settled),
            realized_pnl=str(sum((Decimal(r['pnl']) for r in settled),Decimal(0))))
    return out

class MinuteQuotes:
    """Reuse the same completed quotes/bars across lanes; books are always fresh."""
    def __init__(self,get):self.get=get;self.minute=None;self.cache={}
    def begin(self,T):
        if self.minute!=T:self.minute=T;self.cache={}
    def __call__(self,url):
        if '/orderbook' in url:return self.get(url)
        if url not in self.cache:self.cache[url]=self.get(url)
        return self.cache[url]

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--preflight',action='store_true')
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    STATE.mkdir(exist_ok=True)
    lock=(STATE/'process.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.live and (not (STATE/ARM_FILE).exists() or (STATE/ARM_FILE).read_text().strip()!=str(CONTRACTS)):
        raise RuntimeError('Live activation requires ARM_5CT containing 5')
    if args.live and args.preflight:raise RuntimeError('Preflight must be read-only')
    client=KalshiClientV2(Credentials.load(),dry_run=not args.live,timeout=5)
    engine=Engine(Exchange(client),Store(STATE/'orders.sqlite3'),STATE,live=args.live,permission_check=live_permission)
    if args.preflight:
        engine.reconcile()
        positions,orders,balance=engine.account();reason=legacy_safe()
        if reason:raise RuntimeError(reason)
        if engine.halt_reason():raise RuntimeError(engine.halt_reason())
        if any(r['status'] in ACTIVE for r in engine.store.all()):raise RuntimeError('Existing strategy state requires reconciliation')
        if positions or orders:raise RuntimeError('Crypto account exposure must be reconciled before activation')
        if balance<PREFLIGHT_CASH:raise RuntimeError(f'Less than ${PREFLIGHT_CASH} free cash')
        print(json.dumps(dict(preflight='PASS',crypto_positions=0,crypto_resting_orders=0,
                              free_cash=str(balance),contracts=CONTRACTS,live_orders_enabled=False)))
        return
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    quotes=MinuteQuotes(market_data.get);market_data.get=quotes
    scans={};minute=None;done=set();cached={};checks={};last_reconcile=0.;last_status=0.;error=''
    LOG.info('Start mode=%s, %s contracts per market, max %s, $%s exposure, $%s daily budget; signal ask <95c, execution cap 93.9c', 'LIVE' if args.live else 'OBSERVE', CONTRACTS, MAX_CONTRACTS, MAX_RISK, DAILY_LOSS_BUDGET)
    try:
        while not stopping:
            now=time.time();T=int(now//60)*60
            if T!=minute:minute=T;done=set();cached={};checks={};quotes.begin(T)
            try:
                if now-last_reconcile>=10:
                    engine.reconcile();engine.account();last_reconcile=time.time()
                environment_block=legacy_safe() if args.live else ''
                if args.live and (not (STATE/ARM_FILE).exists() or (STATE/ARM_FILE).read_text().strip()!=str(CONTRACTS)):environment_block='Live arm removed or invalid'
                if environment_block:engine.fault=environment_block
                if 2<=time.time()-T<=30 or args.once:
                    # All original-lane assets have first opportunity each pass.
                    for lane,asset in [(n,a) for n in (Z2,Z3) for a in market_data.ASSETS]:
                        key=(lane,asset)
                        if lane==Z3 and (Z2,asset) not in done:continue
                        if key in done or stopping:continue
                        entered={r['ticker'] for r in engine.store.all()}
                        try:
                            rec=market_data.refresh_book(cached[key]) if key in cached else market_data.scan(asset,T,entered,lane=lane)
                        except Exception as exc:
                            if lane==Z2:raise
                            rec=dict(asset=asset,lane=lane,decision_ts=T,observed_ts=time.time(),status='data_error',error=str(exc)[:200])
                            scans[key]=rec;engine.store.event(rec);done.add(key)
                            LOG.warning('z3 public scan failed: %s',exc)
                            continue
                        checks[key]=checks.get(key,0)+1
                        rec['execution_check_number']=checks[key]
                        if rec['status'] in ('paper_fill','book_outside_limit','insufficient_depth'):
                            cached[key]=dict(rec)
                        original=rec['status']
                        rec=display_signal(rec,'LIVE' if args.live else 'OBSERVE')
                        if original=='paper_fill':
                            rec['status']='eligible';rec['mode']='LIVE' if args.live else 'OBSERVE'
                            # attempt() accepts the validated probe record tag.
                            attempt_signal=dict(rec,status='paper_fill')
                            reason=engine.attempt(attempt_signal)
                            rec.update({k:v for k,v in attempt_signal.items() if k.startswith('execution_')})
                            rec['execution_status']=reason
                            if reason in ('ack','Order reconciliation pending'):
                                engine.reconcile()
                                last_reconcile=time.time()
                        else:reason=''
                        scans[key]=rec;engine.store.event(rec)
                        LOG.info('%s %s %s %s',lane,asset,rec['status'],reason)
                        retry_reason='Book weakened below floor' if rec.get('observed_ask',1)<float(LANES[lane].floor) else reason
                        retry=retry_allowed(original,retry_reason,checks[key],time.time()-T,bool(engine.store.get(rec.get('ticker',''))))
                        if not retry or time.time()-T>25:done.add(key)
                error=''
            except Exception as e:
                error=str(e)[:400];engine.fault=error
                LOG.exception('Cycle failed; entry blocked')
            if time.time()-last_status>=3 or args.once:
                atomic(STATE/'status.json',status(engine,scans,'LIVE' if args.live else 'OBSERVE',error))
                last_status=time.time()
            if args.once:break
            time.sleep(1)
    finally:
        # IOC orders cannot intentionally rest. Reconcile any unexpected
        # remainder by order ID, never cancel other strategies' orders.
        if args.live:engine.reconcile()
        atomic(STATE/'status.json',status(engine,scans,'LIVE' if args.live else 'OBSERVE',error,stopped=True))
        lock.close()

if __name__=='__main__':main()
