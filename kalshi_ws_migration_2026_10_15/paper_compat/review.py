"""Read-only ledger review; saves immutable timestamped reports only."""
import argparse,collections,datetime as dt,gzip,json,sqlite3,time,statistics,random
from pathlib import Path
from decimal import Decimal as D
from paper import number,canonical,fee,day
P=Path(__file__).resolve().parent

def summarize(rows):
    fills=[r for r in rows if number(r.get('filled',0))>0]
    settled=[r for r in fills if r.get('settled')]
    net=sum(number(r['pnl']) for r in settled)
    cum=peak=draw=D(0)
    for r in sorted(settled,key=lambda r:(r['settled_ts'],r['ticker'])):
        cum+=number(r['pnl']);peak=max(peak,cum);draw=max(draw,peak-cum)
    return dict(opportunities=len(rows),attempted=sum(r['state']!='skipped' for r in rows),states=dict(collections.Counter(r['state'] for r in rows)),end_reasons=dict(collections.Counter(r.get('end_reason','pending') for r in rows)),filled_markets=len(fills),partial_fills=sum(0<number(r['filled'])<5 for r in fills),filled_contracts=str(sum(number(r['filled']) for r in fills)),settled=len(settled),wins=sum(r['side']==r['result'] for r in settled),losses=sum(r['side']!=r['result'] for r in settled),net_after_fees=str(net),fees=str(sum(number(r['fees']) for r in settled)),net_after_1c_friction=str(sum(number(r.get('pnl_after_1c_friction',r['pnl'])) for r in settled)),net_taker_fee_stress=str(sum(number(r.get('pnl_taker_fee_stress',r['pnl'])) for r in settled)),settled_drawdown=str(draw),unknown_coverage=sum(not r.get('coverage_complete') for r in rows),quote_touches_without_fill=sum(r.get('quote_touch_observed',False) and number(r.get('filled',0))==0 for r in rows))

def run(root=P):
    root=Path(root);db=sqlite3.connect((root/'paper.sqlite').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    meta={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
    orders=[json.loads(r[0]) for r in db.execute('SELECT record FROM orders')]
    actual={r['ticker']:json.loads(r['record']) for r in db.execute('SELECT * FROM actual')}
    signals={r['ticker']:json.loads(r['record']) for r in db.execute('SELECT * FROM signals')}
    errors=[dict(r) for r in db.execute('SELECT kind,count(*) n FROM log GROUP BY kind')]
    receipts=[dict(r) for r in db.execute('SELECT status,count(*) n,avg(received-started)*1000 mean_ms,max(received-started)*1000 max_ms FROM receipts GROUP BY status')]
    db.close();cfg=json.loads((root/'frozen_config.json').read_text());result=dict(ts=time.time(),started=meta['started'],entry_cutoff=meta['started']+86400*cfg['entry_days'],health=meta.get('health'),signals=len(signals),variants={},comparisons={},by_asset={},actual_live={},errors=errors,http=receipts,validation=[])
    live=[r for r in actual.values() if number(r.get('filled',0))>0]
    result['actual_live']=dict(filled=len(live),settled=sum(r.get('status')=='settled' for r in live),net_after_fees=str(sum(number(r.get('pnl',0)) for r in live if r.get('status')=='settled')),live_fills_without_captured_signal=sum(r['ticker'] not in signals for r in live))
    controls={r['ticker']:r for r in orders if r['variant']=='paper_ioc'}
    for variant in [v['name'] for v in cfg['variants']]+['paper_ioc']:
        rows=[r for r in orders if r['variant']==variant];result['variants'][variant]=summarize(rows)
        result['by_asset'][variant]={a:summarize([r for r in rows if r['asset']==a]) for a in cfg['assets']}
        result.setdefault('by_lane',{})[variant]={a:summarize([r for r in rows if r['lane']==a]) for a in ('cushion_z2','premium_z3')}
        paired=[];missed=[];unknown=[];avoided=[];paperonly=[]
        for r in rows:
            a=actual.get(r['ticker']);q=number(r['filled'])
            if a and number(a.get('filled',0))>0:
                aq=number(a['filled'])
                if q>0 and a['side']==r['side']:
                    paired.append(dict(ticker=r['ticker'],quantity=str(min(q,aq)),live_entry=str(number(a['cost'])/aq),paper_entry=r['price'],improvement_cents=str((number(a['cost'])/aq-number(r['price']))*100),live_pnl=a.get('pnl'),paper_pnl=r['pnl'] if r.get('settled') else None))
                elif q==0 and r['state'] not in ('resting','waiting_control'):
                    item=dict(ticker=r['ticker'],reason=r.get('end_reason'),live_pnl=a.get('pnl'),live_outcome=a.get('result'),paper_side=r['side'])
                    if r.get('coverage_complete'):missed.append(item)
                    else:unknown.append(item)
                    if a.get('status')=='settled' and number(a.get('pnl',0))<0:avoided.append(item)
            elif q>0:paperonly.append(r['ticker'])
        comparable=[r for r in rows if r.get('outcome_recorded') and r.get('coverage_complete') and (r['ticker'] not in actual or number(actual[r['ticker']].get('filled',0))==0 or actual[r['ticker']].get('status')=='settled')]
        cohort_live=sum(number(actual[r['ticker']].get('pnl',0)) for r in comparable if r['ticker'] in actual and actual[r['ticker']].get('status')=='settled')
        cohort_paper=sum(number(r['pnl']) for r in comparable)
        qty=sum(number(x['quantity']) for x in paired)
        # Opportunity ledger includes zero-fill windows, not only cheap fills.
        result['comparisons'][variant]=dict(matched_same_side=len(paired),weighted_entry_improvement_cents=str(sum(number(x['quantity'])*number(x['improvement_cents']) for x in paired)/qty) if qty else None,live_filled_paper_unfilled_complete=len(missed),live_filled_paper_unfilled_unknown=len(unknown),missed_live_winners=sum(x.get('live_pnl') is not None and number(x['live_pnl'])>0 for x in missed),missed_live_winner_pnl=str(sum(number(x['live_pnl']) for x in missed if x.get('live_pnl') is not None and number(x['live_pnl'])>0)),avoided_live_losses_complete=sum(x in missed for x in avoided),comparable_settled_windows=len(comparable),comparable_live_net=str(cohort_live),comparable_paper_net=str(cohort_paper),comparable_delta=str(cohort_paper-cohort_live),paper_only_fills=paperonly,paired=paired,missed=missed,inconclusive=unknown)
        days={};date=dt.date.fromisoformat(day(meta['started']));end=dt.date.fromisoformat(day(min(result['ts'],result['entry_cutoff'])))
        while date<=end:days[str(date)]={'delta':0.,'filled':0,'opportunities':0};date+=dt.timedelta(days=1)
        for r in rows:
            d=day(r['created_ts']);days.setdefault(d,{'delta':0.,'filled':0,'opportunities':0});days[d]['opportunities']+=1;days[d]['filled']+=number(r['filled'])>0
        for r in comparable:
            d=day(r['created_ts']);a=actual.get(r['ticker'],{});days[d]['delta']+=float(number(r['pnl'])-number(a.get('pnl',0)))
        result['comparisons'][variant]['calendar_days']=days
        result['comparisons'][variant]['filled_per_elapsed_24h']=result['variants'][variant]['filled_markets']/max((min(result['ts'],result['entry_cutoff'])-meta['started'])/86400,1/86400)
        if len(days)>=7 and len(comparable)>=100:
            rng=random.Random(141026);values=[x['delta'] for x in days.values()];samples=sorted(sum(rng.choices(values,k=len(values))) for _ in range(10000))
            result['comparisons'][variant]['calendar_day_bootstrap_delta_95']=[samples[249],samples[9749]]
            result['comparisons'][variant]['bootstrap_note']='Exploratory paired total-net interval, clustered by calendar day including zero-entry days; incomplete-coverage windows excluded and reported separately.'
        if variant!='paper_ioc':
            both=[r for r in rows if r.get('outcome_recorded') and r.get('coverage_complete') and controls.get(r['ticker'],{}).get('outcome_recorded') and controls[r['ticker']].get('coverage_complete')]
            result['comparisons'][variant]['synchronous_control']=dict(settled_windows=len(both),paper_net=str(sum(number(r['pnl']) for r in both)),ioc_net=str(sum(number(controls[r['ticker']]['pnl']) for r in both)),ioc_filled_rest_unfilled=sum(number(controls[r['ticker']]['filled'])>0 and number(r['filled'])==0 for r in both))
    for r in orders:
        q=number(r['filled']);assert 0<=q<=5
        if q:
            assert number(r['cost'])==q*number(r['price'])
            assert number(r['price'])<=lane_cap(r)
            if r['variant']!='paper_ioc':
                assert sum(number(f['quantity']) for f in r['fill_evidence'])==q
                assert all(r['accepted_ts']<=f['exchange_ts']<r['effective_expiry'] for f in r['fill_evidence'])
                assert number(r['fees'])==fee([[r['price'],r['filled']]],r['fee_meta']['multiplier'],True,r['fee_meta']['fee_type'])
        if r.get('settled'):assert number(r['pnl'])==number(r['payout'])-number(r['cost'])-number(r['fees'])
    result['validation'].append('quantities, price caps, resting fill evidence timestamps, fees and settlement arithmetic PASS')
    delays=[(r['accepted_ts']-r['signal'].get('signal_qualified_ts',r['signal']['observed_ts']))*1000 for r in orders if r['variant']=='rest_3c_10s' and r.get('accepted_ts')]
    result['signal_to_paper_accept_ms']=dict(n=len(delays),median=statistics.median(delays) if delays else None,max=max(delays) if delays else None)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ');dest=root/'reviews'/stamp;dest.mkdir(parents=True,exist_ok=True)
    (dest/'report.json').write_text(json.dumps(result,indent=2)+'\n');(dest/'orders.json').write_text(json.dumps(orders,indent=2)+'\n')
    lines=['RESTING ORDER PAPER TEST — preliminary, simulated execution only',dt.datetime.fromtimestamp(result['ts'],dt.timezone.utc).isoformat(),'',f"Qualified market opportunities: {len(signals)}. Actual live fills since start: {len(live)}.",'Variant | Filled markets | Partial | Settled W/L | Net after fees | +1c friction net | Missed live fills (complete / unknown)']
    for v,m in result['variants'].items():
        c=result['comparisons'][v]
        lines.append(f"{v} | {m['filled_markets']} | {m['partial_fills']} | {m['wins']}/{m['losses']} | ${m['net_after_fees']} | ${m['net_after_1c_friction']} | {c['live_filled_paper_unfilled_complete']} / {c['live_filled_paper_unfilled_unknown']}")
    lines+=['','Trade prints and visible queue are a counterfactual fill model, not exchange fills. Quote touch alone never fills. No financial orders are submitted.','First observed qualifying signal per market; no later re-entry or IOC fallback in resting variants. Frozen z/time/price rules, 5ct, shared25/$25/$20 independently reserved per variant.','Actual live IOC has its existing retries and earlier access to signals. The synchronous one-shot paper IOC isolates some collector delay, but is not a full production retry replay.','Both paper misses of live winners and paper-only losses count. Partial fills keep actual modeled quantity. Incomplete coverage is reported separately, never silently labeled zero-fill proof.','Primary3c/secondary1c,10s; comparison is not selected after seeing outcomes. Maker fee metadata captured; taker fee and1c friction sensitivity retained.','First fills/settlements are operational evidence only. Review after14calendar days and100settled comparable opportunities; at14-day entry cutoff report insufficient evidence if inadequate. No automatic live promotion.','', 'Full per-asset, paired prices, missed winners, lost profits, and raw modeled positions are in report.json and orders.json.']
    (dest/'REPORT.txt').write_text('\n'.join(lines)+'\n');(root/'latest_review.json').write_text(json.dumps(dict(path=str(dest),ts=result['ts']),indent=2)+'\n')
    return result,dest

def lane_cap(r):
    from paper import limit_price
    return limit_price(r['signal'])[1]

if __name__=='__main__':
    result,dest=run();print(json.dumps(dict(report=str(dest/'REPORT.txt'),signals=result['signals'],variants=result['variants'],actual_live=result['actual_live'],health=result['health'])))
