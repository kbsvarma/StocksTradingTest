"""Independent Decimal accounting and descriptive joint-candidate diagnostics."""
import collections as C,gzip,json,math,pickle
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
import simulation as s
from pull import P,ASSETS,ts,END
with gzip.open(P/'ledgers.json.gz','rt') as f:ledgers=json.load(f)
markets=pickle.loads((P/'features.pickle').read_bytes());mm={m['ticker']:m for m in markets}
checks=0;worst_fee_error=0
for config,scenarios in ledgers.items():
    for scenario,assets in scenarios.items():
        for asset,periods in assets.items():
            for period,rows in periods.items():
                seen=set();cash=D(100);pending=[]
                for r in sorted(rows,key=lambda r:(r['entered'],r['ticker'])):
                    for p in pending[:]:
                        if p['settled']<=r['entered']:
                            cash+=D(5)*p['won'];pending.remove(p)
                    assert r['ticker'] not in seen;seen.add(r['ticker'])
                    m=mm[r['ticker']];p=D(str(r['entry']));q=D(r['quantity'])
                    assert q==5 and r['t']<=r['entered']<r['close']<=r['settled']
                    assert r['won']==int(m['result']==int(r['side']==1))
                    assert len(pending)*5+5<=25
                    assert sum(v['reserve'] for v in pending)+r['reserve']<=25+1e-8
                    raw=(D('.07')*D(str(m['multiplier']))*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
                    fee=(q*p+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-q*p
                    worst_fee_error=max(worst_fee_error,abs(float(fee)-r['fee']))
                    assert abs(float(fee)-r['fee'])<1e-9
                    net=q*(D(r['won'])-p)-fee-D(str(r['friction']))
                    assert abs(float(net)-r['net'])<1e-8
                    budget=min(D(str(r['ask']))+D('.01'),D('.975' if r['lane']=='premium' else '.939'))
                    y=budget if r['side']==1 else 1-budget
                    interval=next(v for v in m['ranges'] if D(v['start'])<=y<=D(v['end']))
                    start,step=D(interval['start']),D(interval['step'])
                    rounded=start+((y-start)/step).to_integral_value(rounding=ROUND_FLOOR if r['side']==1 else ROUND_CEILING)*step
                    cap=rounded if r['side']==1 else 1-rounded
                    assert abs(float(cap)-r['cap'])<1e-9 and p<=cap+D('1e-9')
                    cash-=q*p+fee+D(str(r['friction']));assert cash>=-D('1e-8')
                    pending.append(r);checks+=1
                cash+=sum((D(5)*p['won'] for p in pending),D(0))
                assert abs(float(cash)-100-sum(r['net'] for r in rows))<1e-8
audit=json.loads((P/'training_audit.json').read_text())
assert all(r['last_settled']<r['week'] and r['train']>=250 for r in audit)
import analyze as a
pred=pickle.loads((P/'predictions.pickle').read_bytes())
joint={}
for label,mode,stress in [('quote1c','base',.01),('quote2c','base',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)]:
    pool,_=a.opportunities(a.CONFIG[1],pred,mode)
    rows,diag=s.portfolio([r for r in pool if r['asset'] in ('AUDUSD','USDCAD')],stress)
    daycounts=dict(sorted(C.Counter(s.day(r['entered']) for r in rows).items()))
    same_expiry=C.defaultdict(list)
    for r in rows:same_expiry[r['close']].append(r)
    doubles=[rs for rs in same_expiry.values() if len(rs)==2]
    joint[label]=dict(s.metrics(rows),**diag,entry_day_counts=daycounts,distinct_expiries=len(same_expiry),
        two_pair_expiries=len(doubles),same_USD_direction_pairs=sum(rs[0]['side']!=rs[1]['side'] for rs in doubles))
statistics={}
for asset,config in [('EURUSD','eurusd_adaptive'),('USDJPY','strict_z2_z3'),('AUDUSD','eurusd_adaptive'),('USDCAD','eurusd_adaptive')]:
    rows=ledgers[config]['quote1c'][asset]['full'];m=s.metrics(rows);n=len(rows)
    assetmarkets=[x for x in markets if x['asset']==asset]
    total_days=(END-min(m['start'] for m in assetmarkets))/86400
    # Two-sided exact 95% lower bound for an all-win binomial sample; descriptive,
    # since window outcomes are not independent and configurations were researched.
    statistics[asset]=dict(calendar_days=total_days,trades_per_calendar_day=n/total_days,
        observed_win_rate=m['wins']/n,all_in_break_even=m['breakeven'],
        iid_95_lower_win_rate=(.025**(1/n) if m['losses']==0 else None),
        additional_same_average_cost_loss= -5*(m['breakeven']),
        net_with_one_additional_loss=m['net']-5*m['breakeven'],
        net_with_two_additional_losses=m['net']-10*m['breakeven'],
        entry_day_counts=dict(sorted(C.Counter(s.day(r['entered']) for r in rows).items())),
        final_minute_entries=sum(r['rem']==1 for r in rows),
        last_two_minute_entries=sum(r['rem']<=2 for r in rows))
s.save('independent_validation.json',dict(passed=True,scenario_rows=checks,max_fee_error=worst_fee_error,
    causal_training_checks=len(audit),note='Scenario rows overlap and are not independent trades.'))
s.save('candidate_statistics.json',dict(joint=joint,assets=statistics,
    caution='Standalone FX-only simulations; actual crypto/commodity priority and shared daily risk are not included. No claim these totals are incremental live profits. Exact binomial bound assumes IID and does not correct research selection.'))
print('PASS',checks,'rows; joint',joint['quote1c']);print('STATS',statistics)
