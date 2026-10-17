"""Frozen, offline BTC/ETH study. No credentials, order client, or live writes.

Imports read-only arithmetic/replay helpers from the earlier research. All new
outputs are rooted here; the old results and stable checkpoint are untouched.
"""
from pathlib import Path
import bisect, collections, datetime as dt, hashlib, json, math, pickle, sys
import numpy as np

P = Path(__file__).resolve().parent
ROOT = P.parent
sys.path.insert(0, str(ROOT / 'kalshi_parallel_design_2026_10_02'))
import research as old
from strategy import Rule, qualifies, tick_cap, book_fill, fee
from portfolio import portfolio

ts, day, metrics = old.ts, old.day, old.metrics
FIT_END = ts('2026-08-28T00:00:00Z')
SELECTION_END = ts('2026-09-12T00:00:00Z')
LATER_END = ts('2026-10-02T00:00:00Z')
END = ts('2026-10-03T00:00:00Z')
BASE = ['current_signal95', 'premium_z3']
BASE_RULES = [r for r in old.RULES if r.name in BASE]
RULES = [
    Rule('cheaper_z2', .65, .85, 2, 3, 10, .939),
    Rule('early_z2', .70, .95, 2, 11, 13, .939),
    Rule('late_z2', .85, .975, 2, 1, 2, .975),
    Rule('late_z3', .90, .985, 3, 1, 2, .980),
    Rule('gaussian_edge8', .55, .95, 1.5, 3, 10, .939, 'gaussian', .08),
    Rule('student_edge8', .55, .95, 1.5, 3, 10, .939, 'student', .08),
    Rule('adaptive_edge8', .55, .95, 1.5, 3, 10, .939, 'adaptive', .08),
    Rule('adaptive_edge5', .55, .95, 1.5, 3, 10, .939, 'adaptive', .05),
    Rule('basic_edge3', .55, .95, 0, 1, 13, .939, 'basic', .03),
    Rule('basic_edge5', .55, .95, 0, 1, 13, .939, 'basic', .05),
    Rule('rich_edge3', .55, .95, 0, 1, 13, .939, 'rich', .03),
    Rule('rich_edge5', .55, .95, 0, 1, 13, .939, 'rich', .05),
    Rule('official_z2', .85, .95, 2, 3, 10, .939, 'official'),
    Rule('official_average_edge5', .55, .95, 1.5, 1, 13, .939, 'official_average', .05),
]

def cohort(t):
    return 'fit' if t < FIT_END else 'selection' if t < SELECTION_END else 'later' if t < LATER_END else 'fresh'

def phi(z):
    return .5 * (1 + math.erf(z / math.sqrt(2)))

def logit(p):
    p = min(.999, max(.001, p))
    return math.log(p / (1-p))

def save(name, value):
    (P / name).write_text(json.dumps(value, indent=2, allow_nan=False))

def read_inputs():
    folders = old.FOLDERS + [P / 'fresh_data']
    # Redirect this helper's documented coverage output to this new study only.
    old.P, old.FOLDERS = P, folders
    markets = [m for m in old.load_data() if ts('2026-08-13T00:00:00Z') <= m['close'] < END]
    meta = {}; bars = {a: {} for a in old.ASSETS}
    for folder in folders:
        for a in old.ASSETS:
            mp = folder / f'markets_{a}.jsonl'
            if mp.exists():
                for line in mp.open():
                    r = json.loads(line); meta[r['ticker']] = r
            sp = folder / f'spot_{a}.jsonl'
            if sp.exists():
                for line in sp.open():
                    for r in json.loads(line)['rows']: bars[a][int(r[0])] = float(r[4])
    cf = {a: {} for a in ('BTC', 'ETH')}
    for line in (folders[0] / 'benchmark.jsonl').open():
        r = json.loads(line)
        if r['asset'] in cf:
            for t, last, avg in r['minutes']: cf[r['asset']][t] = float(last)
    # A later supplemental CF file, if deliberately fetched, is additive only.
    extra = P / 'fresh_data' / 'benchmark.jsonl'
    if extra.exists():
        for line in extra.open():
            r = json.loads(line)
            if r['asset'] in cf:
                for t, last, avg in r['minutes']: cf[r['asset']][t] = float(last)
    return markets, meta, bars, cf

def stats(bars):
    out = {}
    for a, bb in bars.items():
        keys = np.arange(min(bb), max(bb)+60, 60)
        v = np.array([bb.get(int(t), np.nan) for t in keys])
        ret = np.diff(np.log(v))
        ws = np.lib.stride_tricks.sliding_window_view(ret, 60)
        valid = np.isfinite(ws).all(axis=1)
        sig = np.std(ws, axis=1); sig15 = np.std(ws[:, -15:], axis=1)
        out[a] = {int(keys[i+60]+60): (float(v[i+60]), float(sig[i]), float(sig15[i]),
            float(ws[i,-3:].sum()), float(ws[i,-5:].sum())) for i in np.flatnonzero(valid & (sig > 0))}
    return out

def augment(markets, meta, bars, cf):
    ss, cc = stats(bars), stats(cf)
    missing = collections.Counter()
    for m in markets:
        if m['asset'] not in ('BTC', 'ETH'): continue
        a = m['asset']; other = 'ETH' if a == 'BTC' else 'BTC'
        for f in m['features']:
            s = ss[a].get(f['t']); o = ss[other].get(f['t'])
            prev = old.quote(m['candles'].get(f['t']-60))
            if s is None or o is None or prev is None:
                missing['model_inputs'] += 1; continue
            assert abs(s[0] - f['spot']) < 1e-8
            assert abs(s[1] - f['sigma']) < 1e-10
            mid = (f['bid'] + f['ask']) / 2
            prevmid = sum(prev) / 2
            if f['side'] == -1: prevmid = 1 - prevmid
            f['ratio'] = s[2] / s[1]
            f['xbasic'] = [1., logit(mid), max(-5., min(5., f['z'])), math.log(f['rem']/7), float(a == 'ETH')]
            v = float(m['candles'].get(f['t'], {}).get('v') or 0)
            f['xrich'] = f['xbasic'] + [math.log(max(.05, min(20., f['ratio']))),
                f['side']*s[3]/(s[1]*math.sqrt(3)), f['side']*s[4]/(s[1]*math.sqrt(5)),
                f['side']*o[3]/(o[1]*math.sqrt(3)), logit(mid)-logit(prevmid), math.log1p(v)]
        for f in m['features']:
            c = cc[a].get(f['t']); strike = meta[m['ticker']].get('floor_strike')
            if c is None or not strike: missing['official_inputs'] += 1; continue
            distance = f['side'] * (c[0]/float(strike) - 1)
            f['zofficial'] = distance / (c[1]*math.sqrt(f['rem']))
            f['zaverage'] = distance / (c[1]*math.sqrt(f['rem']-2/3))
    return dict(missing)

def fit_models(markets):
    models = {}
    for kind in ('basic', 'rich'):
        xx, yy, ww = [], [], []
        for m in markets:
            if m['asset'] not in ('BTC','ETH') or m['close'] >= FIT_END: continue
            fs = [f for f in m['features'] if 'x'+kind in f and .55 <= f['ask'] < .98 and f['ask']-f['bid'] <= .02000001]
            for f in fs:
                xx.append(f['x'+kind]); yy.append(int(f['side']==m['result'])); ww.append(1/len(fs))
        raw = np.array(xx); y = np.array(yy); w = np.array(ww)
        mean = np.average(raw, axis=0, weights=w); scale = np.sqrt(np.average((raw-mean)**2, axis=0, weights=w))
        # Preserve intercept and logit-midpoint scale for the market-price prior.
        mean[:2]=0; scale[:2]=1; scale[scale<1e-8]=1
        X = (raw-mean)/scale
        coef=np.zeros(X.shape[1]); coef[1]=1; prior=coef.copy()
        penalty=np.eye(len(coef))*10; penalty[0,0]=1
        for _ in range(80):
            pred=1/(1+np.exp(-np.clip(X@coef,-30,30)))
            grad=X.T@(w*(pred-y))+penalty@(coef-prior)
            hessian=(X.T*(w*pred*(1-pred)))@X+penalty
            step=np.linalg.solve(hessian,grad);coef-=step
            if np.max(np.abs(step))<1e-9:break
        models[kind] = dict(coefficients=coef.tolist(), mean=mean.tolist(), scale=scale.tolist(), rows=len(y), markets_weight=float(w.sum()), penalty=penalty.diagonal().tolist())
        for m in markets:
            for f in m['features']:
                if 'x'+kind in f:
                    x=(np.array(f['x'+kind])-mean)/scale
                    f['p'+kind]=float(1/(1+np.exp(-np.clip(x@coef,-30,30))))
    save('models.json',models)
    return models

def probabilities(f):
    out = {'market': (f['bid']+f['ask'])/2, 'gaussian': phi(f['z']),
        'student': .5+(math.atan(f['z'])+f['z']/(1+f['z']**2))/math.pi}
    if 'ratio' in f: out['adaptive']=phi(f['z']/max(1.,f['ratio']))
    for kind in ('basic','rich'):
        if 'p'+kind in f: out[kind]=f['p'+kind]
    if 'zaverage' in f: out['official_average']=phi(f['zaverage'])
    return out

def eligible(rule, f):
    z = f.get('zofficial') if rule.kind=='official' else f.get('zaverage') if rule.kind=='official_average' else f['z']
    if z is None or z < rule.zmin: return False
    if not rule.remlo <= f['rem'] <= rule.remhi: return False
    if not rule.lo-1e-8 <= f['ask'] < rule.hi-1e-8: return False
    if f['ask']-f['bid'] > .02000001: return False
    if rule.edge:
        p = probabilities(f).get(rule.kind)
        if p is None or p-f['ask']-fee(f['ask']) < rule.edge: return False
    return True

def candidate_replay(markets, books=None, delay=0):
    out=collections.defaultdict(list); counts=collections.Counter()
    for m in markets:
        if m['asset'] not in ('BTC','ETH'): continue
        bb=books.get(m['ticker'],[]) if books is not None else None
        if bb is not None and not bb: continue
        bts=[b[0] for b in bb] if bb is not None else None
        for rule in RULES:
            for f in m['features']:
                if not eligible(rule,f): continue
                counts[rule.name+'_signals']+=1
                cap=tick_cap(rule,f,m['ranges'])
                if cap is None:continue
                if bb is not None:
                    j=bisect.bisect_left(bts,f['t']+max(.3,delay))
                    if j==len(bb) or bts[j]>f['t']+35:continue
                    fill=book_fill(bb[j],f['side'],cap,rule.lo,5)
                    if fill is None:continue
                    entry,paid=fill;entered=bts[j]
                else:
                    q=old.quote(m['candles'].get(f['t']+60+delay),'o')
                    if q is None:continue
                    entry=q[1] if f['side']==1 else 1-q[0]
                    if not rule.lo-1e-8<=entry<=cap+1e-8:continue
                    paid=fee(entry,5);entered=f['t']+delay+3
                won=int(f['side']==m['result'])
                out[rule.name].append(dict(ticker=m['ticker'],asset=m['asset'],t=f['t'],entered=entered,
                    close=m['close'],settled=max(m['close']+30,m['settled']),day=day(m['close']),cohort=cohort(m['close']),
                    entry=entry,cap=cap,fee=paid,quantity=5,won=won,pnl=5*(won-entry)-paid,
                    pnl_cap=5*(won-cap)-fee(cap,5),z=f['z'],side=f['side'],rem=f['rem'],ask=f['ask']))
    return dict(out),dict(counts)

def resim(rows,names,stress=.01,cap=False):
    return portfolio(rows,names,max_contracts=20,max_risk=19,daily_budget=10,stress=stress,cap_cost=cap)

def calibration(markets):
    out={}
    for co in ('fit','selection','later','fresh'):
        out[co]={}
        for a in ('BTC','ETH'):
            data=[]
            for m in markets:
                if m['asset']!=a or cohort(m['close'])!=co:continue
                fs=[f for f in m['features'] if 'prich' in f and .55<=f['ask']<.98 and f['ask']-f['bid']<=.02000001]
                for f in fs:data.append((probabilities(f),int(f['side']==m['result']),1/len(fs),f['z']))
            result={}
            for kind in ['market','gaussian','student','adaptive','basic','rich','official_average']:
                rr=[(pp[kind],y,w,pp['market']) for pp,y,w,z in data if kind in pp]
                if not rr:continue
                p,y,w,pm=map(np.array,zip(*rr));p=np.clip(p,1e-9,1-1e-9);pm=np.clip(pm,1e-9,1-1e-9)
                result[kind]=dict(n=len(y),market_weight=round(float(w.sum()),3),brier=float(np.average((p-y)**2,weights=w)),logloss=float(np.average(-y*np.log(p)-(1-y)*np.log1p(-p),weights=w)),predicted_win=float(np.average(p,weights=w)),actual_win=float(np.average(y,weights=w)),market_brier_same_rows=float(np.average((pm-y)**2,weights=w)),market_logloss_same_rows=float(np.average(-y*np.log(pm)-(1-y)*np.log1p(-pm),weights=w)))
            bins={}
            for lo,hi in [(0,1),(1,1.5),(1.5,2),(2,3),(3,1000)]:
                rr=[(pp['gaussian'],y,w) for pp,y,w,z in data if lo<=z<hi]
                if rr:
                    p,y,w=map(np.array,zip(*rr)); bins[f'{lo}–{hi}']=dict(observations=len(y),weight=float(w.sum()),expected_loss=float(np.average(1-p,weights=w)),observed_loss=float(np.average(1-y,weights=w)))
            out[co][a]=dict(models=result,z_bins=bins)
    save('calibration.json',out)

def liquidity(markets,meta,books):
    result={}
    for co in ('fit','selection','later','fresh'):
        result[co]={}
        for a in old.ASSETS:
            mm=[m for m in markets if m['asset']==a and cohort(m['close'])==co]
            vols=[float(meta[m['ticker']].get('volume_fp') or 0) for m in mm]
            fs=[f for m in mm for f in m['features'] if 3<=f['rem']<=10]
            depths=collections.Counter()
            for m in mm:
                bb=books.get(m['ticker'],[]);bts=[b[0] for b in bb]
                for f in m['features']:
                    for r in BASE_RULES:
                        if not qualifies(r,f):continue
                        cap=tick_cap(r,f,m['ranges'])
                        if cap is None:continue
                        depths[r.name+'_signals']+=1
                        j=bisect.bisect_left(bts,f['t']+.3)
                        if j==len(bb) or bts[j]>f['t']+35:continue
                        depths[r.name+'_subsequent_book']+=1
                        for q in (1,5,10,20):
                            if book_fill(bb[j],f['side'],cap,r.lo,q):depths[r.name+f'_depth{q}']+=1
            result[co][a]=dict(markets=len(mm),median_volume=float(np.median(vols)) if vols else None,total_volume=sum(vols),quotes=len(fs),spread_le_2c=sum(f['ask']-f['bid']<=.02000001 for f in fs),depth=dict(depths))
    save('liquidity.json',result)

def summarize_scenario(markets,rows,scenario):
    result={};trades={}
    for co in ('fit','selection','later','fresh'):
        if 'book' in scenario and co in ('later','fresh'):continue
        rr={k:[r for r in v if cohort(r['close'])==co] for k,v in rows.items()}
        baseline,blocked=resim(rr,BASE);baseids={r['ticker'] for r in baseline}
        result[co]={'baseline':dict(metrics(baseline),blocked=blocked),'candidates':{}}
        trades[co]={'baseline':baseline}
        for a in ('BOTH','BTC','ETH'):
            for rule in RULES:
                # Missing official history is explicitly absent, not a zero-edge result.
                cand=[r for r in rr.get(rule.name,[]) if a=='BOTH' or r['asset']==a]
                if rule.kind.startswith('official') and co in ('later','fresh') and not cand:continue
                inputs={**{k:rr.get(k,[]) for k in BASE},rule.name:cand}
                joint,blocked=resim(inputs,BASE+[rule.name]);solo,_=resim(inputs,[rule.name])
                cap,_=resim(inputs,BASE+[rule.name],cap=True)
                stressed,_=resim(inputs,BASE+[rule.name],stress=.02)
                bcap,_=resim(inputs,BASE,cap=True);bstress,_=resim(inputs,BASE,stress=.02)
                ids={r['ticker'] for r in joint};j=metrics(joint);b=metrics(baseline)
                key=rule.name+':'+a
                result[co]['candidates'][key]=dict(standalone=metrics(solo),joint=j,
                    incremental_net=round(j['net']-b['net'],5),incremental_trades=j['n']-b['n'],
                    extra_markets=len(ids-baseids),displaced_baseline_markets=len(baseids-ids),
                    cap_incremental_net=round(metrics(cap)['net']-metrics(bcap)['net'],5),
                    stress2c_incremental_net=round(metrics(stressed,.02)['net']-metrics(bstress,.02)['net'],5),
                    candidate_lane=metrics([r for r in joint if r['lane']==rule.name]),blocked=blocked)
                trades[co][key]=joint
        print(scenario,co,'baseline',result[co]['baseline'],flush=True)
    save(scenario+'_summary.json',result)
    (P/(scenario+'_trades.pickle')).write_bytes(pickle.dumps(trades,protocol=5))
    return result

def main():
    markets,meta,bars,cf=read_inputs()
    print('Loaded',len(markets),'markets',flush=True)
    missing=augment(markets,meta,bars,cf);save('feature_missing.json',missing)
    fit_models(markets);calibration(markets)
    (P/'features.pickle').write_bytes(pickle.dumps(markets,protocol=5))
    books=old.load_books();liquidity(markets,meta,books)
    for scenario,bb,delay in [('quote',None,0),('book',books,0),('book_delay10',books,10),('quote_delay60',None,60)]:
        base,_=old.replay(markets,None,bb,quantity=5,delay=delay,all_signals=True,rules=BASE_RULES)
        candidates,counts=candidate_replay(markets,bb,delay)
        summary=summarize_scenario(markets,{**base,**candidates},scenario)
        save(scenario+'_signal_counts.json',counts)
        if scenario=='quote':
            assert summary['later']['baseline']['n']==302,summary['later']['baseline']
            assert abs(summary['later']['baseline']['net']-65.9172)<.0001,summary['later']['baseline']
    save('run_manifest.json',dict(markets=len(markets),candidate_rules=14,asset_screens=42,
        plan_sha256=hashlib.sha256((P/'PLAN.md').read_bytes()).hexdigest(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        baseline_regression='302 trades / $65.9172, matching previously validated production-engine replay',
        input_note='All source hashes are in coverage.json; scripts import no authenticated or order client.'))

if __name__=='__main__': main()
