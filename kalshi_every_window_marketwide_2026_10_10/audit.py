"""Independent Decimal ledger verification; does not submit orders."""
from common import *
from core import day
from decimal import Decimal as D,ROUND_CEILING
from collections import Counter,defaultdict
counts=Counter();errors=[]
def check(ok,message):
    counts['assertions']+=1
    if not ok and len(errors)<100:errors.append(message)
def near(x,y):return abs(float(x)-float(y))<1e-7
datasets=[(P,s) for s in ['segment','hourly_fifteen_weather','pregame','followup','daily','lines','joint','pooled']]+[(P/'daily_extension','daily'),(P/'wnba_extension','pregame')]
import sys
passive_only='passive' in sys.argv
if passive_only:datasets=[(P,'passive')]
for root,stem in datasets:
    lp=root/(stem+'_ledgers.json.gz');rp=root/(stem+'_results.json')
    if not lp.exists():continue
    ledgers=read(lp);results=read(rp)
    for key,rows in ledgers.items():
        cash=D(100);seen=set();active=[];daily=defaultdict(lambda:D(0));total=D(0)
        for r in rows:
            label=stem+'/'+key+'/'+r['owner'];q=D(r['qty']);p=D(str(r['entry'])).quantize(D('.0001'));mult=D(str(r['mult']));fric=D(str(r['friction']))
            f=(D('.07')*q*p*(1-p)*mult*10000).to_integral_value(rounding=ROUND_CEILING)/10000;cost=q*(p+fric)+f;net=q*D(str(r['payout']))-cost
            check(near(f,r['fee']) and near(cost,r['cost']) and near(net,r['net']),label+' arithmetic')
            check(r['owner'] not in seen,label+' duplicate owner');seen.add(r['owner'])
            check(r['t']<r['entered']<min(r['settled'],r['close']),label+' chronology')
            if r['kind']=='passive_proxy':
                check(.01<=r['entry']<=r['cap']+1e-8 and .01<=r['ask']<=.99 and r['ask']-r['bid']<=.05000001 and r['volume10']>=5,label+' passive entry constraints')
                if r['fill_type'].startswith('unverified'):
                    check(120<=r['entered']-r['t']<=300 and r['entry']<=r['ask']-.01+1e-8 and r['through_price']<=r['entry']-.01*r['through_cents']+1e-8 and r['mult']==0,label+' unverified proxy conditions')
                else:check(r['entered']==r['t']+303 and r['mult']==r['original_mult'],label+' fallback conditions')
            else:check(r['ask']<=r['entry']<=r['cap']+1e-8 and .01<=r['ask']<=.99 and r['ask']-r['bid']<=.05000001 and r['volume10']>=5,label+' entry constraints')
            if r['kind']=='segment':
                check(r['source_wall']+r['lag']<=r['t'] and r['execution_source_wall']+r['lag']<=r['entered'] and r['source_age']<=120 and r['entered']-r['execution_source_wall']<=120 and r['entered']<r['segment_end'],label+' source chronology')
            if r['kind']=='pregame':
                check(r['entered']<r['scheduled_start'] and (r['calibration_last_settlement'] is None or r['calibration_last_settlement']<r['calibration_boundary']<=r['t']),label+' pregame calibration')
            if r['kind']=='daily':
                check(r['calibration_last_settlement'] is None or r['calibration_last_settlement']<r['calibration_boundary']<=r['t'],label+' daily calibration')
            if r['kind']=='followup':
                check(r['window_first']<=r['t']<=r['window_deadline'],label+' followup window')
            if r['kind']=='lines':
                check(r['entered']<r['scheduled_start'],label+' pregame line')
            if r['kind'] in ('hourly','15minute','weather') and not r.get('model_fallback'):
                check(r['source_last']<r['t'],label+' future source')
                if r['kind']=='weather':check(r['calibration_end']<=r['t'] and r['calibration_n']>=60,label+' weather calibration')
            for a,ac,an in active:
                if a['settled']<=r['entered']:cash+=D(a['qty'])*D(str(a['payout']));daily[day(a['settled'])]+=an
            active=[a for a in active if a[0]['settled']>r['entered']];risk=sum((a[1] for a in active),D(0))+cost
            check(sum(a[0]['qty'] for a in active)+r['qty']<=25 and risk<=25+D('1e-8') and daily[day(r['entered'])]-risk>=-20-D('1e-8') and cash-cost>=2-D('1e-8'),label+' bankroll risk')
            cash-=cost;active.append((r,cost,net));total+=net;counts['ledger_rows']+=1
        check(near(total,results[key]['net']) and len(rows)==results[key]['trades'],stem+'/'+key+' totals');counts['scenarios']+=1
out={'passed':not errors,'counts':dict(counts),'errors':errors,'limits':['Repeated scenario rows are not independent trades.','Quote candles do not establish historical five-contract depth or first-received source times.']}
save(P/('passive_audit.json' if passive_only else 'audit.json'),out);print(json.dumps(out,indent=2));assert not errors
