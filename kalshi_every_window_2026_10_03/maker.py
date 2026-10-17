"""Trade-through passive-entry research, never assumes touch equals fill."""
import argparse, collections, datetime as dt, gzip, json, pickle, re
import screen as s
P=s.P
def timestamp(t):
    t=re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),t)
    return dt.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def tape(ticker):
    with gzip.open(P/'maker_tapes'/(ticker+'.json.gz'),'rt') as h:d=json.load(h)
    rows=[r for page in d['pages'] for r in page.get('trades',[])]
    for r in rows:r['_t']=timestamp(r['created_time'])
    return sorted(rows,key=lambda r:r['_t'])
def through(rows,T,dr,limit):
    count=0;early=0;filltime=None
    for r in rows:
        t=r['_t'] if '_t' in r else timestamp(r['created_time']);aggressor=r.get('taker_side')
        if aggressor!=('no' if dr==1 else 'yes'):continue
        px=float(r['yes_price_dollars'] if dr==1 else r['no_price_dollars'])
        if px>=limit-1e-8:continue
        if t<T+1:early+=float(r['count_fp']);continue
        if t>T+61:continue
        qty=min(1-count,float(r['count_fp']));count+=qty
        if count>=1-1e-8:filltime=t;break
    return count,filltime,early
def make(m,policy,tapes,method):
    f=m['new_features'].get(12)
    if not f:return None
    dr=s.side(f,policy)
    if dr is None:return None
    if method=='immediate':return s.trade(m,f,dr)
    if method=='delay_only':return s.trade(m,dict(f,t=f['t']+60),dr)
    limit=f['b'] if dr==1 else 1-f['a']
    if not 0<limit<1:return None
    qty,filled,early=through(tapes,f['t'],dr,limit)
    if method=='maker_exclude_early_cross' and early>0:return None
    tr=s.trade(m,f,dr)
    if tr is None:return None
    fallback=None if qty>=1-1e-8 else s.trade(m,dict(f,t=f['t']+60),dr)
    if qty<1-1e-8 and fallback is None:
        if qty<=0:return None
        # Preserve partial maker exposure, do not erase it when fallback lacks quote.
        n=qty;price=qty*limit;paid=0;entered=f['t']+1
    else:
        n=1;price=qty*limit+(1-qty)*(fallback['entry'] if fallback else 0)
        paid=s.fee(fallback['entry'],1-qty) if fallback else 0;entered=filled if qty>=1-1e-8 else f['t']+63
    makerfee=s.fee(limit,qty,maker=True) if method=='maker_fee_stress' and qty else 0
    tr.update(entry=price/n if n!=1 else price,entered=entered,quantity=n,fee=paid+makerfee,
        pnl=n*tr['exit']-price-paid-makerfee,maker_quantity=qty,fallback_quantity=n-qty,early_cross=early,limit=limit)
    return tr
def dual(m,rows):
    f=m['new_features'].get(12)
    if not f:return None
    quantities=[];cost=0;pay=0;early=0
    for dr,limit in [(1,f['b']),(-1,1-f['a'])]:
        if not 0<limit<1:continue
        n,tt,ec=through(rows,f['t'],dr,limit);early+=ec
        if n:quantities.append(n);cost+=n*limit;pay+=n*int(dr==m['result'])
    if not quantities:return None
    n=sum(quantities)
    return dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],day=s.day(m['close']),cohort=m['cohort'],decision=f['t'],entered=f['t']+1,
        side=0,entry=cost/n,exit=pay/n,exit_ts=max(m['close']+1,m['settled']),quantity=n,fee=0,pnl=pay-cost,
        settlement_win=int(pay>=cost),legs=1,exit_reason='settlement',maker_quantity=n,fallback_quantity=0,both=len(quantities)==2,early_cross=early)
def screen(ms,cohorts,full=False):
    spec=json.loads((P/('maker_full_sample.json' if full else 'maker_sample.json')).read_text());keys={x['ticker'] for x in spec};rows={};den=collections.Counter()
    for m in ms:
        if m['ticker'] not in keys or m['cohort'] not in cohorts:continue
        den[m['cohort']]+=1;tt=tape(m['ticker'])
        for policy in ('favorite','spot_momentum'):
            for method in ('immediate','delay_only','maker_first','maker_fee_stress','maker_exclude_early_cross'):
                name=policy+'/'+method;rows.setdefault(name,[]);tr=make(m,policy,tt,method)
                if tr:rows[name].append(tr)
        rows.setdefault('dual_maker',[]);tr=dual(m,tt)
        if tr:rows['dual_maker'].append(tr)
    out={k:{c:s.metrics([r for r in rr if r['cohort']==c],den[c]) for c in cohorts} for k,rr in rows.items()}
    for k,rr in rows.items():
        for c in cohorts:
            subset=[r for r in rr if r['cohort']==c]
            out[k][c]['maker_quantity']=sum(r.get('maker_quantity',0) for r in subset)
            out[k][c]['fallback_quantity']=sum(r.get('fallback_quantity',0) for r in subset)
            out[k][c]['both_filled_windows']=sum(r.get('both',False) for r in subset)
    return out,rows
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['select','test']);ap.add_argument('--full',action='store_true');a=ap.parse_args()
    ms=pickle.loads((P/'prepared.pickle').read_bytes())
    prefix='maker_full_' if a.full else 'maker_'
    out,rows=screen(ms,['fit','selection'] if a.mode=='select' else ['test','supplement'],a.full);s.save(prefix+a.mode+'.json',out)
    with gzip.open(P/(prefix+a.mode+'_trades.json.gz'),'wt') as h:json.dump(rows,h)
    if a.mode=='select':
        candidates=[k for k in out if k.endswith('/maker_first') and all(out[k][c]['net']>0 and out[k][c]['coverage']>=.9 for c in ('fit','selection'))]
        choice=max(candidates,key=lambda k:out[k]['selection']['net']) if candidates else None
        s.save(prefix+'frozen.json',dict(chosen=choice,passing=candidates))
    print(json.dumps({k:{c:{q:v[q] for q in ['n','net','drawdown','maker_quantity','fallback_quantity']} for c,v in cc.items()} for k,cc in out.items()},indent=2))
