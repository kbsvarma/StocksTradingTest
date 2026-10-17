"""Predeclared, observational rain replay. NO trading/order interfaces."""
import csv, gzip, json, math, re, sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ROOT=P.parent
OLD=ROOT/'kalshi_every_window_marketwide_2026_10_10'
sys.path.insert(0,str(OLD))
from core import qte,ts,stats,day

def save(n,x):(P/n).write_text(json.dumps(x,indent=2))
def fee(p,q):return math.ceil(.07*q*p*(1-p)*100-1e-9)/100
def simulate(rows,friction,enforce):
    cash=100;active=[];seen=set();real=defaultdict(float);out=[];blocked=Counter()
    for r in sorted(rows,key=lambda x:(x['entered'],x['owner'])):
        if r['owner'] in seen:continue
        for a in active:
            if a['settled']<=r['entered']:cash+=5*a['payout'];real[day(a['settled'])]+=a['net']
        active=[a for a in active if a['settled']>r['entered']]
        f=fee(r['entry'],5);cost=5*(r['entry']+friction)+f;reserve=sum(a['cost'] for a in active)+cost
        reason=''
        if enforce:
            if 5*(len(active)+1)>25:reason='contracts'
            elif reserve>25+1e-8:reason='exposure'
            elif real[day(r['entered'])]-reserve < -20-1e-8:reason='daily_risk'
            elif cash-cost<2:reason='cash'
        if reason:blocked[reason]+=1;continue
        seen.add(r['owner']);a=dict(r,qty=5,fee=f,cost=cost,friction=friction,net=5*r['payout']-cost)
        cash-=cost;active.append(a);out.append(a)
    return out,dict(blocked)

def main():
    meta=json.load(open(P/'station_metadata.json'))['response']['results']
    mapping={r['station']['cliId']:r['station'] for r in meta}
    zones={v['icao'][1:]:ZoneInfo(v['timezone']) for v in mapping.values()}
    obs=defaultdict(list);raw=Counter()
    with open(P/'precip.csv') as f:
        for r in csv.DictReader(f):
            try:v=float(r['p01i'])
            except ValueError:raw['missing_or_trace']+=1;continue
            dt=datetime.fromisoformat(r['valid']).replace(tzinfo=timezone.utc);local=dt.astimezone(zones[r['station']])
            raw['valid']+=1
            # Exclude the first two local hours so the hourly accumulation does
            # not cross either local midnight or the standard-time boundary.
            if local.hour>=2:obs[(r['station'],local.date().isoformat())].append((int(dt.timestamp()),v))
    index=[g for g in json.load(open(OLD/'daily_index.json')) if g['asset']=='KXRAIN']
    qc={};rows=defaultdict(list);skips=defaultdict(Counter);audit=[]
    for g in index:
        m=g['markets'][0];ident=re.search(r'at (CLI[A-Z]{3}|K[A-Z]{3}) ',m['rules_primary']).group(1).removeprefix('CLI').removeprefix('K');station=mapping[ident]['icao'][1:]
        source=obs.get((station,g['date']),[]);first={level:min((t for t,v in source if v>=level),default=None) for level in [.01,.02,.05]}
        audit.append(dict(ticker=m['ticker'],date=g['date'],station=station,source_reports=len(source),first_positive=first,settled=m['result']))
        for timing,t in enumerate(g['times']):
            if t not in qc:
                path=OLD/'daily_quotes'/f'{t}.json.gz'
                qc[t]={k:{c['end_period_ts']:c for c in v} for k,v in json.load(gzip.open(path,'rt')).items()} if path.exists() else {}
            cs=qc[t].get(m['ticker'],{})
            for level,lag in [(.02,30),(.01,30),(.05,30),(.02,15),(.02,60)]:
                name=f'p{level:.2f}_lag{lag}'
                if not source:skips[name]['source_missing']+=1;continue
                if first[level] is None or first[level]+lag*60>t:skips[name]['no_available_positive_reading']+=1;continue
                if not ts(m['open_time'])<=t<ts(m['close_time']):skips[name]['not_open']+=1;continue
                q=qte(cs.get(t))
                if q is None:skips[name]['missing_signal_quote']+=1;continue
                if not .01<=q[1]<=.97:skips[name]['price_above_97c_or_invalid']+=1;continue
                if q[1]-q[0]>.0500001:skips[name]['spread']+=1;continue
                volume=sum(float(c.get('volume_fp') or 0) for s,c in cs.items() if t-600<s<=t)
                if volume<5:skips[name]['volume']+=1;continue
                for mode,phase in [('next_open','open'),('delay60','close')]:
                    key=name+'/'+mode;q2=qte(cs.get(t+60),phase=phase)
                    if q2 is None:skips[key]['missing_subsequent_quote']+=1;continue
                    price=max(q[1],q2[1]);cap=min(.97,q[1]+.01)
                    if price>cap+1e-9:skips[key]['execution_cap']+=1;continue
                    entered=t+(60 if mode=='delay60' else 3)
                    if entered>=min(ts(m['close_time']),ts(m['settlement_ts'])):skips[key]['closed']+=1;continue
                    rows[key].append(dict(asset=station,owner=m['ticker'],ticker=m['ticker'],date=g['date'],t=t,entered=entered,entry=price,signal_ask=q[1],next_ask=q2[1],cap=cap,settled=ts(m['settlement_ts']),payout=float(m['result']=='yes'),source_t=first[level],volume10=volume,timing=timing))
    results={};ledgers={}
    for level,lag in [(.02,30),(.01,30),(.05,30),(.02,15),(.02,60)]:
        for mode in ['next_open','delay60']:
            name=f'p{level:.2f}_lag{lag}/{mode}'
            for friction in [.01,.02]:
                for enforce in [True,False]:
                    key=name+f'/cost{friction}/'+('budgeted' if enforce else 'all_opportunities')
                    led,blocked=simulate(rows[name],friction,enforce);ledgers[key]=led
                    results[key]=dict(**stats(led),calendar_days=56,trades_per_calendar_day=len(led)/56,blocked=blocked,
                        cities={c:stats([r for r in led if r['asset']==c]) for c in sorted(set(r['asset'] for r in led))},
                        earlier=stats([r for r in led if r['date']<'2026-10-01']),later=stats([r for r in led if r['date']>='2026-10-01']))
    save('rain_replay_results.json',results);save('rain_replay_ledgers.json',ledgers);save('rain_replay_skips.json',skips);save('rain_source_audit.json',audit)
    for k,v in results.items():
        if '/cost0.01/' in k:print(k,{a:v[a] for a in ['trades','wins','losses','net','drawdown']},flush=True)
    print('STATION_AGREEMENT',dict(Counter((a['settled'],a['first_positive'][.02] is not None) for a in audit)),flush=True)

def pair_check():
    pairs=json.load(open(P/'rain_pairs.json'));books=json.load(open(P/'rain_book_receipts.json'));out=[]
    def buy(ticker,side):
        b=books[ticker]['response']['orderbook_fp'];levels=b['no_dollars' if side=='yes' else 'yes_dollars'];left=5.;cost=fees=0.;fills=[]
        for bid,sz in sorted(((float(p),float(q)) for p,q in levels),reverse=True):
            q=min(left,sz);price=round(1-bid,4);cost+=q*price;fees+=.07*q*price*(1-price);left-=q;fills.append([q,price])
            if left<1e-8:break
        return None if left>1e-8 else dict(cost=cost,fees=math.ceil(fees*100-1e-9)/100,fills=fills)
    for p in pairs:
        a=p['day']['ticker'];b=p['weekend']['ticker'];da=buy(a,'no');wb=buy(b,'yes')
        row=dict(city=p['city'],day=a,weekend=b,day_no=da,weekend_yes=wb,receipt_gap_seconds=abs(books[a]['received']-books[b]['received']))
        if da and wb:row.update(min_payout=5,net_floor_before_friction=5-da['cost']-wb['cost']-da['fees']-wb['fees'],net_floor_after_friction=5-da['cost']-wb['cost']-da['fees']-wb['fees']-.1)
        out.append(row)
    save('rain_pair_results.json',out)
    print('PAIR_TEST',len(out),'depth_complete',sum('net_floor_after_friction' in x for x in out),'positive',sum(x.get('net_floor_after_friction',-100)>0 for x in out))
    print('BEST_PAIRS',sorted(out,key=lambda x:x.get('net_floor_after_friction',-100),reverse=True)[:3])

if __name__=='__main__':main();pair_check()
