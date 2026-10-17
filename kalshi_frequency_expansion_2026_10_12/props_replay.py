"""Causal pregame props calibration and exact payout accounting; offline only."""
from core import *
import argparse, math, pickle
START=ts('2026-09-01T04:00:00Z')
PERIODS={'selection':(ts('2026-09-15T04:00:00Z'),ts('2026-10-01T04:00:00Z')),
         'later':(ts('2026-10-01T04:00:00Z'),ts('2026-10-12T04:00:00Z')),
         'continuous':(ts('2026-09-15T04:00:00Z'),ts('2026-10-12T04:00:00Z'))}
RULES=('fixed_yes','fixed_no','calibrated')
def prepare():
    records=[];coverage=C.Counter();audit=[]
    for game in read(P/'props_index.json'):
        t=game['t'];cmap=read(P/'data/props_quotes'/f"{game['event']}.json.gz");athletes=C.defaultdict(list)
        for m in game['markets']:
            coverage['contracts']+=1
            cm={int(c['end_period_ts']):c for c in cmap.get(m['ticker'],[])}
            q=quote(cm.get(t));coverage['quoted_at_decision']+=q is not None
            if q is None:continue
            if q[1]-q[0]>.050000001:coverage['wide']+=1;continue
            vol=sum(float(cm.get(t-j*60,{}).get('volume_fp') or 0) for j in range(10))
            if vol<5:coverage['prior_volume']+=1;continue
            settled=ts(m['settlement_ts']);payout=float(m['settlement_value_dollars'])
            if settled<=t or not 0<=payout<=1:coverage['settlement_invalid']+=1;continue
            player=m['player'];mid=sum(q)/2
            r=dict(owner=f"{game['game']}:{game['asset']}:{player}",game=game['game'],asset=game['asset'],
                   ticker=m['ticker'],t=t,exit_t=settled,yes_payout=payout,mult=game['mult'],mid=mid,
                   bid=q[0],ask=q[1],volume=vol,next_open=quote(cm.get(t+60),'open'),next_close=quote(cm.get(t+60)),
                   bin=min(9,int(mid*10)))
            athletes[player].append(r)
        for rows in athletes.values():
            closest=min(rows,key=lambda r:(abs(r['mid']-.5),r['ticker']))
            for r in rows:r['closest']=r is closest
            records+=rows;coverage['athlete_games']+=1
    models={};weeks=sorted({(r['t']-345600)//604800*604800+345600 for r in records})
    for week in weeks:
        train=C.defaultdict(list)
        for r in records:
            if r['closest'] and r['exit_t']<week:train[r['asset'],r['bin']].append(r)
        for (asset,b),rr in train.items():
            if len(rr)<200:continue
            corr=sum(r['yes_payout']-r['mid'] for r in rr)/(len(rr)+100)
            models[week,asset,b]=corr
            audit.append(dict(week=week,asset=asset,bin=b,n=len(rr),last_settlement=max(r['exit_t'] for r in rr),correction=corr))
    for r in records:
        w=(r['t']-345600)//604800*604800+345600;corr=models.get((w,r['asset'],r['bin']))
        r['prob']=max(0,min(1,r['mid']+corr)) if corr is not None else None
    (P/'props_features.pickle').write_bytes(pickle.dumps(records))
    save('props_feature_coverage.json',coverage);save('props_model_audit.json',audit)
    print('FEATURES',dict(coverage),'models',len(audit),flush=True)
def candidates(records,rule,mode):
    groups=C.defaultdict(list);diag=C.Counter()
    for r in records:groups[r['owner']].append(r)
    out=[]
    for owner,rows in groups.items():
        choices=[]
        for r in rows:
            if rule!='calibrated' and not r['closest']:continue
            for side in ((1,) if rule=='fixed_yes' else (-1,) if rule=='fixed_no' else (1,-1)):
                ask=r['ask'] if side==1 else 1-r['bid'];prob=r['prob'] if side==1 else 1-r['prob'] if r['prob'] is not None else None
                if not .1-1e-8<=ask<=.9+1e-8:continue
                edge=None if prob is None else prob-ask-fee(ask,1,r['mult'])-.01
                if rule=='calibrated' and (edge is None or edge<.02):continue
                choices.append((edge if edge is not None else 0,r['ticker'],side,r,ask))
        if not choices:continue
        _,_,side,r,ask=max(choices,key=lambda x:(x[0],x[1],x[2]));diag['qualified_athletes']+=1
        limit=min(.92,round(ask+.02,8));q=r['next_close' if mode=='delay60' else 'next_open']
        if q is None:diag['missing_next_quote']+=1;continue
        bookask=q[1] if side==1 else 1-q[0];entry=max(ask,bookask)
        if entry>limit+1e-8:diag['outside_limit']+=1;continue
        delay=60 if mode=='delay60' else 3
        if r['exit_t']<=r['t']+delay:diag['settled_before_entry']+=1;continue
        out.append(dict(owner=owner,game=r['game'],asset=r['asset'],ticker=r['ticker'],side=side,t=r['t'],entry_t=r['t']+delay,
                        entry=round(entry,8),limit=limit,exit_t=r['exit_t'],exit_price=r['yes_payout'] if side==1 else 1-r['yes_payout'],
                        exit_kind='settlement',mult=r['mult'],prob=r['prob'],signal_ask=ask))
    return out,dict(diag)
def run(mode):
    records=pickle.load(open(P/'props_features.pickle','rb'));result={};ledgers={}
    periods=['selection'] if mode=='select' else ['later','continuous']
    for rule in RULES:
        result[rule]={};ledgers[rule]={}
        for name,book,cost in [('base','base',.01),('cost2','base',.02),('delay60','delay60',.01),('delay60_cost2','delay60',.02)]:
            pool,diag=candidates(records,rule,book);result[rule][name]={};ledgers[rule][name]={}
            for label in periods:
                lo,hi=PERIODS[label];rows,account=portfolio([r for r in pool if lo<=r['t']<hi],friction=cost)
                m=metrics(rows,(hi-lo)/86400);m.update(account);m.update(candidate_diagnostics=diag,unique_games=len({r['game'] for r in rows}))
                result[rule][name][label]=m;ledgers[rule][name][label]=rows
        print(mode,rule,{p:{k:result[rule]['base'][p][k] for k in ('trades','net','trades_per_day')} for p in periods},flush=True)
    save('props_'+mode+'_results.json',result);save('props_'+mode+'_ledgers.json.gz',ledgers)
    if mode=='select':
        passing=[rule for rule in RULES if all(result[rule][s]['selection']['net']>0 for s in ('base','cost2','delay60_cost2')) and result[rule]['base']['selection']['trades_per_day']>=10]
        save('props_selection_frozen.json',dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),passing=passing,
             selected=max(passing,key=lambda k:result[k]['cost2']['selection']['net']) if passing else None,
             source_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),later_not_evaluated=True))
    else:assert (P/'props_selection_frozen.json').exists()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','select','evaluate']);args=parser.parse_args()
    prepare() if args.mode=='prepare' else run(args.mode)
