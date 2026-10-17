"""Run observation by default. Live needs --live and ARM_5CT; new z3 entries also need ARM_Z3_5CT."""
from __future__ import annotations
import argparse
from decimal import Decimal
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import signal
import time
from urllib.parse import parse_qs, urlparse
import yaml
from sizing import CONTRACTS, MAX_CONTRACTS, PREFLIGHT_CASH, ARM_FILE
from engine import Engine,Exchange,Store,ACTIVE,MAX_RISK,DAILY_LOSS_BUDGET
from kalshi_client import Credentials,KalshiClientV2
from account_reads import FreshAccountReads, ACCOUNT_READ_BUDGET_SECONDS
import market_data
from execution_retry import retry_allowed, retry_candidate, fresh_retry_candidate, WTI_FRESH_RETRY_VERSION, USDCAD_FRESH_RETRY_VERSION, FRESH_RETRY_ASSETS, ZERO_FILL_RETRY, MAX_IOC_SUBMISSIONS, MAX_PRE_SUBMISSION_CHECKS, RETRY_DEADLINE_SECONDS, PRIMARY_HEAD_START_CHECKS
from io_budget import get_budget
from lanes import LANES, Z2, Z3, lane_for, z3_permission, Z3_MAX_CONTRACTS, Z3_MAX_RISK, Z3_DAILY_BUDGET, NEW_ASSETS, CRYPTO_ASSETS, ALL_ASSETS, ACTIVE_ASSETS, route_for, asset_permission, MULTIASSET_VERSION

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
        execution_path_version=1,account_reads='parallel_fresh_get',
        strategy='multiasset_15m_v2',multiasset_version=MULTIASSET_VERSION,assets=asset_status(engine,rows,mode),lanes=lane_status(engine,rows,mode),contracts=CONTRACTS,max_contracts=int(MAX_CONTRACTS),max_risk=str(MAX_RISK),
        daily_loss_budget=str(DAILY_LOSS_BUDGET),band='85–<95¢ signal; max 93.9¢ buy',signal_ask_ceiling_dollars='0.95',execution_cap_dollars='0.939',z_min=2,minutes='3–10',
        execution='Capped IOC; max 3 submissions per market after confirmed zero fills; WTI and USD/CAD may requalify on a later minute; 25s per signal; hold to settlement',
        wti_fresh_retry_version=WTI_FRESH_RETRY_VERSION,
        usdcad_fresh_retry_version=USDCAD_FRESH_RETRY_VERSION, fresh_retry_assets=list(FRESH_RETRY_ASSETS),
        account_read_deadline_seconds=ACCOUNT_READ_BUDGET_SECONDS,
        execution_checks_max=MAX_PRE_SUBMISSION_CHECKS,execution_retry_deadline_seconds=RETRY_DEADLINE_SECONDS,
        max_ioc_submissions=MAX_IOC_SUBMISSIONS,zero_fill_retry_enabled=True,
        pending_reconcile_seconds=1,
        armed=(STATE/ARM_FILE).exists(),halted=engine.halt_reason() or ('Order reconciliation pending' if pending else ''),error=error,
        account=engine.last_account,scans=[display_signal(r,mode) for r in scans.values()],
        active=[r for r in rows if r['status'] in ACTIVE],
        realized_pnl=sum(float(r.get('pnl') or 0) for r in rows),
        settled=sum(r['status']=='settled' for r in rows),attempts=sum(r.get('attempt_number',1) for r in rows),markets_attempted=len(rows),
        trades=rows[-100:])

def asset_status(engine,rows,mode):
    out={}
    for asset in ACTIVE_ASSETS:
        block=asset_permission(STATE,asset)
        own=[r for r in rows if r.get('asset')==asset or r['ticker'].startswith('KX'+asset+'15M-')]
        rules={}
        for name in (Z2,Z3):
            rule=lane_for(dict(asset=asset,lane=name))
            rules[name]=dict(z_base_at_5m=rule.z,z_slope=rule.z_slope,min_minutes=rule.min_minutes,
                max_minutes=rule.max_minutes,signal_floor=str(rule.floor),signal_ceiling=str(rule.ceiling),execution_cap=str(rule.cap))
        out[asset]=dict(enabled=mode=='LIVE' and not block and not engine.halt_reason(),blocked=block,
            contracts=5,exchange_index=route_for(dict(asset=asset)),rules=rules,
            attempts=sum(r.get('attempt_number',1) for r in own),settled=sum(r['status']=='settled' for r in own))
    return out

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
            attempts=sum(r.get('attempt_number',1) for r in own),markets_attempted=len(own),settled=len(settled),
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
        if '/orderbook' in url or '/live_data/' in url:return self.get(url)
        if url in self.cache:return self.cache[url]
        response=self.get(url)
        if self.cacheable(url,response):self.cache[url]=response
        return response

    @staticmethod
    def cacheable(url,response):
        # Freeze a usable completed quote, never a temporarily absent candle.
        if 'api.exchange.coinbase.com/' in url and '/candles?' in url:
            try:
                target=int(parse_qs(urlparse(url).query)['end'][0])
                bars={int(r[0]):float(r[4]) for r in response}
                return all(k in bars and math.isfinite(bars[k]) and bars[k]>0 for k in range(target-3660,target,60))
            except (KeyError,TypeError,ValueError,IndexError):return False
        if '/candlesticks?' not in url:return True
        try:
            target=int(parse_qs(urlparse(url).query)['end_ts'][0])
            candle=next(c for c in response['candlesticks'] if c['end_period_ts']==target)
            bid=float(candle['yes_bid']['close_dollars']);ask=float(candle['yes_ask']['close_dollars'])
            return math.isfinite(bid) and math.isfinite(ask) and 0<=bid<=ask<=1
        except (KeyError,TypeError,ValueError,StopIteration):return False

def scan_ready(lane,asset,done,checks,age):
    key=(lane,asset)
    if key in done:return False
    # Do not dispatch a queued retry after the scheduling deadline.
    if checks.get(key,0) and age>RETRY_DEADLINE_SECONDS:return False
    # Preserve the original eight-check head start, without making z3 wait
    # longer merely because z2 now has additional tail checks.
    return lane!=Z3 or (Z2,asset) in done or checks.get((Z2,asset),0)>=PRIMARY_HEAD_START_CHECKS

def execution_result(engine,ticker,reason):
    """Reconciliation, not acknowledgment, decides whether a zero-fill retry exists."""
    if reason in ('ack','Order reconciliation pending'):
        engine.reconcile()
    row=engine.store.get(ticker)
    if row and row['status'] in ('intent','unknown','ack','accounting_pending'):
        return 'Order reconciliation pending'
    if row:
        if row['status']=='unfilled':
            if retry_candidate(row,row.get('signal',{}).get('decision_ts'),engine.clock(),row.get('lane')):
                return ZERO_FILL_RETRY
            return 'Canceled with zero fills; retries exhausted or closed'
        if row['status']=='open':return 'Filled '+str(row.get('filled','0'))+' contracts'
        if row['status']=='settled':return 'Settled '+str(row.get('filled','0'))+' contracts'
        if row['status'] in ('unknown','ack','accounting_pending'):return 'Order reconciliation pending'
    return reason

def has_pending_reconciliation(engine):
    return any(r['status'] in ('intent','unknown','ack','accounting_pending') for r in engine.store.all())

def refresh_fx_cash(engine):
    # Display-only refresh. Every actual entry still reads its route's cash
    # again in Engine.account; a failed FX refresh cannot halt funded crypto.
    if asset_permission(STATE,'EURUSD'):return
    account=engine.last_account or {}
    try:
        cash=engine.exchange.balance(exchange_index=0)
        account.setdefault('balances',{})['0']=str(cash)
        account.pop('fx_cash_error',None)
    except Exception as exc:
        account.setdefault('balances',{}).pop('0',None)
        account['fx_cash_error']=str(exc)[:160]
    engine.last_account=account

def retry_reason_for(engine,rec,reason):
    if rec.get('observed_ask',1)<float(lane_for(rec).floor):return 'Book weakened below floor'
    # Keep other frozen signals alive while an accepted order is indexing.
    # The engine's fault and entry guards remain authoritative on every check.
    if engine.fault and has_pending_reconciliation(engine) and not engine.store.get(rec.get('ticker','')):
        return 'Order reconciliation pending'
    return reason

def ownership_status(rec,store):
    if rec.get('status')!='already_entered':return rec
    row=store.get(rec['ticker']) or {}
    if row.get('status')=='unfilled':
        rec['status']='zero_fill_locked'
        rec['execution_status']='Canceled with zero fills; '+(
            'retry belongs to '+row.get('lane','cushion_z2') if (retry_candidate(row,rec['decision_ts'],time.time()) or fresh_retry_candidate(row,rec['decision_ts'],time.time()))
            else 'retry limit/window closed')
    elif row.get('status') in ('intent','unknown','ack','accounting_pending'):
        rec['status']='order_pending';rec['execution_status']='Waiting for exchange reconciliation'
    elif row.get('status')=='rejected':
        rec['status']='order_rejected';rec['execution_status']=row.get('error','Order rejected')
    return rec

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
    account_reader=FreshAccountReads(client)
    engine=Engine(Exchange(client,account_reader=account_reader),Store(STATE/'orders.sqlite3'),STATE,live=args.live,permission_check=live_permission)
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
        account_reader.close()
        return
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    quotes=MinuteQuotes(market_data.get);market_data.get=quotes
    warm_errors=market_data.warm_sources();last_warm=time.time()
    if warm_errors:LOG.warning('Underlying warmup pending: %s',warm_errors)
    scans={};minute=None;done=set();cached={};checks={};last_reconcile=0.;last_status=0.;error=''
    LOG.info('Start mode=%s, %s contracts per market, max %s, $%s exposure, $%s daily budget; signal ask <95c, execution cap 93.9c', 'LIVE' if args.live else 'OBSERVE', CONTRACTS, MAX_CONTRACTS, MAX_RISK, DAILY_LOSS_BUDGET)
    try:
        while not stopping:
            now=time.time();T=int(now//60)*60
            if T!=minute:minute=T;done=set();cached={};checks={};quotes.begin(T)
            try:
                # Warm historical source bars outside the submission window.
                # Successful scans still fetch/freeze this decision's own chart.
                if time.time()-last_warm>=300 and 36<=time.time()-T<=45:
                    warm_errors=market_data.warm_sources();last_warm=time.time()
                if now-last_reconcile >= (1 if has_pending_reconciliation(engine) else 10):
                    engine.reconcile();engine.account();last_reconcile=time.time()
                    if not has_pending_reconciliation(engine):refresh_fx_cash(engine)
                environment_block=legacy_safe() if args.live else ''
                if args.live and (not (STATE/ARM_FILE).exists() or (STATE/ARM_FILE).read_text().strip()!=str(CONTRACTS)):environment_block='Live arm removed or invalid'
                if environment_block:engine.fault=environment_block
                if 2<=time.time()-T<=30 or args.once:
                    # All original-lane assets have first opportunity each pass.
                    for lane,asset in [(n,a) for n in (Z2,Z3) for a in market_data.ASSETS]:
                        key=(lane,asset)
                        if stopping or not scan_ready(lane,asset,done,checks,time.time()-T):continue
                        block=asset_permission(STATE,asset) if args.live else ''
                        if block:
                            scans[key]=dict(asset=asset,lane=lane,decision_ts=T,observed_ts=time.time(),status='activation_blocked',execution_status=block)
                            done.add(key);continue
                        entered=engine.entered_markets(lane,T)
                        try:
                            if key not in cached:
                                recovered=engine.retry_signal(asset,lane,T)
                                if recovered:cached[key]=recovered
                            with get_budget(min(3,max(0,T+30-time.time()))):
                                rec=market_data.refresh_book(cached[key]) if key in cached else market_data.scan(asset,T,entered,lane=lane)
                        except Exception as exc:
                            rec=dict(asset=asset,lane=lane,decision_ts=T,observed_ts=time.time(),status='data_error',error=str(exc)[:200])
                            scans[key]=rec;engine.store.event(rec)
                            checks[key]=checks.get(key,0)+1
                            if checks[key]>=MAX_PRE_SUBMISSION_CHECKS or time.time()-T>RETRY_DEADLINE_SECONDS:done.add(key)
                            LOG.warning('%s %s public scan failed: %s',lane,asset,exc)
                            continue
                        checks[key]=checks.get(key,0)+1
                        rec['execution_check_number']=checks[key]
                        if rec['status'] in ('paper_fill','book_outside_limit','insufficient_depth'):
                            cached[key]=dict(rec)
                        original=rec['status']
                        rec=ownership_status(display_signal(rec,'LIVE' if args.live else 'OBSERVE'),engine.store)
                        if original=='paper_fill':
                            rec['status']='eligible';rec['mode']='LIVE' if args.live else 'OBSERVE'
                            # attempt() accepts the validated probe record tag.
                            attempt_signal=dict(rec,status='paper_fill')
                            reason=engine.attempt(attempt_signal)
                            rec.update({k:v for k,v in attempt_signal.items() if k.startswith('execution_')})
                            if reason in ('ack','Order reconciliation pending'):
                                reason=execution_result(engine,rec['ticker'],reason)
                                last_reconcile=time.time()
                            elif reason=='Already attempted this market':
                                reason=execution_result(engine,rec['ticker'],reason)
                            rec['execution_status']=reason
                            order=engine.store.get(rec['ticker'])
                            if order:rec['order_attempt_number']=order.get('attempt_number',1)
                        else:reason=''
                        scans[key]=rec;engine.store.event(rec)
                        LOG.info('%s %s %s %s',lane,asset,rec['status'],reason)
                        retry_reason=retry_reason_for(engine,rec,reason)
                        order=engine.store.get(rec.get('ticker',''))
                        zero=(retry_candidate(order,T,time.time(),lane) or fresh_retry_candidate(order,T,time.time(),lane))
                        pending=bool(order and order['status'] in ('intent','unknown','ack','accounting_pending'))
                        retry=retry_allowed(original,retry_reason,checks[key],time.time()-T,bool(order),zero_fill_retry=zero,awaiting_terminal=pending)
                        if original=='incomplete_spot' and not order:
                            retry=checks[key]<MAX_PRE_SUBMISSION_CHECKS and 0<=time.time()-T<=RETRY_DEADLINE_SECONDS
                        if not retry or time.time()-T>RETRY_DEADLINE_SECONDS:done.add(key)
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
        account_reader.close()
        lock.close()

if __name__=='__main__':main()
