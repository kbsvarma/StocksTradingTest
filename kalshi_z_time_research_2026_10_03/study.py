"""Offline raw-input z/time research. Contains no account or order transport."""
import bisect, collections, datetime as dt, hashlib, json, math, pickle, sys
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from numba import njit

P=Path(__file__).resolve().parent; ROOT=P.parent
SUPPORT=ROOT/'kalshi_parallel_design_2026_10_02'
if not (SUPPORT/'research.py').exists():SUPPORT=P/'support'
sys.path.insert(0,str(SUPPORT))
import research as old
from strategy import Rule, fee, tick_cap, book_fill
from portfolio import portfolio as reference_portfolio
ASSETS=('BTC','ETH','SOL','XRP'); ET=ZoneInfo('America/New_York')
FOLDERS=[ROOT/'kalshi_restart_audit_2026_09_12/fresh_data',ROOT/'kalshi_parallel_design_2026_10_02/fresh_data',ROOT/'kalshi_execution_audit_2026_10_02/fresh_data',ROOT/'kalshi_btc_eth_quant_2026_10_02/fresh_data']
FOLDERS=[p if (p/'markets_BTC.jsonl').exists() else P/'input_sources'/str(i) for i,p in enumerate(FOLDERS)]
BOUNDS=[old.ts(x+'T00:00:00Z') for x in ['2026-08-13','2026-08-28','2026-09-12','2026-10-02','2026-10-03']]
PERIODS=('development','validation','later','october2')
PROFILES=[Rule('lower',.85,.95,0,1,14,.939),Rule('premium',.93,.98,0,1,14,.975)]
ZGRID=[round(.5+.25*i,2) for i in range(15)]+[4.5,5.]
ASSETSETS={'BTC':1,'ETH':2,'SOL':4,'XRP':8,'BTC_ETH':3,'ALL':15}
# Array columns, with identity pointing to the auditable Python opportunity record.
MID,AID,PROFILE,LANE,ENTER,SETTLE,CLOSE,EDAY,SDAY,REM,Z,ENTRY,CAP,WON,FEE,OID=range(16)

def save(name,obj):
    (P/name).write_text(json.dumps(obj,indent=2,allow_nan=False))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def etday(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
def dayid(t):return (dt.date.fromisoformat(etday(t))-dt.date(2026,8,12)).days

def build():
    # Redirect documented coverage output; none of the earlier research is edited.
    old.P=P;old.FOLDERS=FOLDERS
    markets=[m for m in old.load_data() if BOUNDS[0]<=m['close']<BOUNDS[-1]]
    bars={a:{} for a in ASSETS}
    for folder in FOLDERS:
        for a in ASSETS:
            path=folder/f'spot_{a}.jsonl'
            if path.exists():
                for line in path.open():
                    for row in json.loads(line)['rows']:bars[a][int(row[0])]=float(row[4])
    added=collections.Counter(); parity=0; maxdiff=0.
    prior=ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle'
    if not prior.exists():prior=P/'prior_features.pickle'
    previous={m['ticker']:m for m in pickle.loads(prior.read_bytes())}
    for mid,m in enumerate(markets):
        m['mid']=mid;T=m['start']+60;rem=(m['close']-T)//60
        assert rem==14
        q=old.quote(m['candles'].get(T));bb=bars[m['asset']]
        prices=[bb.get(t) for t in range(T-3660,T,60)];ref=bb.get(m['start']-60)
        if q is None:added['missing_quote']+=1
        elif ref is None or any(v is None or not math.isfinite(v) or v<=0 for v in prices):added['incomplete_spot']+=1
        else:
            sig=float(np.std(np.diff(np.log(prices))))
            if sig>0:
                side=1 if sum(q)>1 else -1;b,a=q if side==1 else (1-q[1],1-q[0])
                m['features'].insert(0,dict(t=T,rem=rem,side=side,bid=b,ask=a,z=side*(prices[-1]/ref-1)/(sig*math.sqrt(rem)),spot=prices[-1],ref=ref,sigma=sig))
                added['added14']+=1
        prev={f['t']:f for f in previous[m['ticker']]['features']}
        for f in m['features']:
            if f['rem']==14:continue
            p=prev[f['t']]
            for k in ['side','bid','ask','z','spot','ref','sigma']:
                diff=abs(f[k]-p[k]);maxdiff=max(maxdiff,diff);assert diff<1e-9,(m['ticker'],k,diff)
            parity+=1
    (P/'features.pickle').write_bytes(pickle.dumps(markets,protocol=5))
    save('feature_validation.json',dict(markets=len(markets),old_feature_comparisons=parity,max_abs_difference=maxdiff,minute14=dict(added),features_by_minute=dict(collections.Counter(f['rem'] for m in markets for f in m['features'])),plan_sha256=digest(P/'PLAN.md')))
    print('features',len(markets),parity,dict(added),flush=True)
    return markets

def opportunities(markets,books=None,delay=.3,deadline=25.,rechecks=True):
    """Quote proxy at next candle OPEN; book path only observed post-signal depth."""
    records=[];arr=[];counts=collections.Counter()
    for m in markets:
        bb=books.get(m['ticker'],[]) if books is not None else None
        times=[b[0] for b in bb] if bb is not None else None
        for f in m['features']:
            for prof,r in enumerate(PROFILES):
                if not r.lo-1e-8<=f['ask']<r.hi-1e-8 or f['ask']-f['bid']>.02000001:continue
                counts['price_spread_minutes']+=1
                cap=tick_cap(r,f,m['ranges'])
                if cap is None:continue
                if bb is not None:
                    j=bisect.bisect_left(times,f['t']+delay);fill=None
                    while j<len(bb) and times[j]<=f['t']+deadline and times[j]<=f['t']+35:
                        fill=book_fill(bb[j],f['side'],cap,r.lo,5)
                        if fill is not None:break
                        if not rechecks:break
                        j+=1
                    if fill is None:continue
                    price,paid=fill;entered=times[j]
                else:
                    q=old.quote(m['candles'].get(f['t']+60),'o')
                    if q is None:continue
                    price=q[1] if f['side']==1 else 1-q[0]
                    if not r.lo-1e-8<=price<=cap+1e-8:continue
                    paid=fee(price,5);entered=f['t']+3
                won=int(f['side']==m['result']);settled=max(m['close']+30,m['settled'])
                record=dict(ticker=m['ticker'],asset=m['asset'],t=f['t'],entered=entered,close=m['close'],settled=settled,day=old.day(m['close']),cohort=PERIODS[next(i for i in range(4) if BOUNDS[i]<=m['close']<BOUNDS[i+1])],entry=price,cap=cap,fee=paid,quantity=5,won=won,pnl=5*(won-price)-paid,pnl_cap=5*(won-cap)-fee(cap,5),z=f['z'],side=f['side'],rem=f['rem'],ask=f['ask'],profile=prof,volume=m['candles'].get(f['t'],{}).get('v'))
                oid=len(records);records.append(record)
                base=[m['mid'],ASSETS.index(m['asset']),prof,2,entered,settled,m['close'],dayid(entered),dayid(settled),f['rem'],f['z'],price,cap,won,paid,oid]
                arr.append(base)
                if 3<=f['rem']<=10 and f['z']>=(2 if prof==0 else 3):
                    clone=base.copy();clone[LANE]=prof;arr.append(clone)
    a=np.array(arr,dtype=np.float64).reshape((-1,16))
    a=a[np.lexsort((a[:,AID],a[:,LANE],a[:,ENTER]))]
    return records,a,dict(counts)

@njit(cache=True)
def simulate(a,profile=-1,zmin=99.,lo=1,hi=14,mask=15,slope=0.,stress=.01,capcost=False,third_n=5,centfee=False,miss_rate=0.,miss_seed=0):
    """Single owner, causal settlement/risk, baseline lanes plus optional third."""
    seen=np.zeros(20000,np.bool_);active=np.full(20,-1,np.int64);qty=np.zeros(20);pnl=np.zeros(20)
    realized=np.zeros(100);ownrealized=np.zeros(100);cash=50.;accepted=np.empty(len(a),np.int64);nacc=0
    for j in range(len(a)):
        r=a[j];lane=int(r[LANE]);pro=int(r[PROFILE])
        if lane==2:
            if pro!=profile or not ((1<<int(r[AID])) & mask) or not lo<=r[REM]<=hi:continue
            threshold=max(.5,zmin+slope*math.log2(r[REM]/5.))
            if r[Z]<threshold:continue
        n=third_n if lane==2 else 5
        for k in range(20):
            if active[k]>=0:
                p=a[active[k]]
                if p[SETTLE]<=r[ENTER]:
                    cash+=qty[k]*p[WON];realized[int(p[SDAY])]+=pnl[k]
                    if p[LANE]==1:ownrealized[int(p[SDAY])]+=pnl[k]
                    active[k]=-1
        if seen[int(r[MID])]:continue
        count=0.;risk=0.;owncount=0.;assetblock=False
        for k in range(20):
            if active[k]>=0:
                p=a[active[k]];count+=qty[k];risk+=qty[k]*(.95 if p[PROFILE]==0 else 1.)
                if p[AID]==r[AID]:assetblock=True
                if p[LANE]==1:owncount+=qty[k]
        reserve=.95 if pro==0 else 1.
        projected=risk+reserve*n
        if assetblock or count+n>20 or projected>19+1e-8:continue
        if realized[int(r[EDAY])]-projected < -10-1e-8:continue
        if lane==1 and (owncount+n>20 or ownrealized[int(r[EDAY])]-(owncount+n)<-25-1e-8):continue
        if cash<max(5.,n*reserve)-1e-8:continue
        if miss_rate>0:
            draw=math.sin((r[MID]+1)*12.9898+(miss_seed+1)*78.233)*43758.5453
            if draw-math.floor(draw)<miss_rate:
                # An unsuccessful IOC still consumes market ownership; no later retry.
                seen[int(r[MID])]=True
                continue
        price=r[CAP] if capcost else r[ENTRY]
        # Reuse exact per-book fee for size5; cap/other-size/cent rounding recomputed.
        paid=r[FEE]
        if capcost or n!=5 or centfee:
            unit=.01 if centfee else .0001
            paid=math.ceil((n*price+.07*n*price*(1-price)-1e-10)/unit)*unit-n*price
        cost=n*price+paid+stress*n
        cash-=cost
        for k in range(20):
            if active[k]<0:active[k]=j;qty[k]=n;pnl[k]=n*r[WON]-cost;break
        seen[int(r[MID])]=True;accepted[nacc]=j;nacc+=1
    return accepted[:nacc]

@njit(cache=True)
def faststats(a,indices):
    n=len(indices);wins=0;net=0.;dd=0.;high=0.;third=0;thirdnet=0.;eq=0.
    order=np.argsort(a[indices,CLOSE]);previous=-1.;chunk=0.
    for k in order:
        r=a[indices[k]];p=5*(r[WON]-r[ENTRY]-.01)-r[FEE]
        wins+=int(r[WON]);net+=p
        if r[LANE]==2:third+=1;thirdnet+=p
        if r[CLOSE]!=previous:
            eq+=chunk;high=max(high,eq);dd=max(dd,high-eq);chunk=0.;previous=r[CLOSE]
        chunk+=p
    eq+=chunk;high=max(high,eq);dd=max(dd,high-eq)
    return float(n),float(wins),net,dd,float(third),thirdnet

@njit(cache=True)
def batch(a,configs):
    out=np.empty((len(configs),6))
    for i in range(len(configs)):
        c=configs[i];ix=simulate(a,int(c[0]),c[1],int(c[2]),int(c[3]),int(c[4]),c[5])
        values=faststats(a,ix)
        for j in range(6):out[i,j]=values[j]
    return out

def configlist():
    configs=[]
    for pro in range(2):
        for name,mask in ASSETSETS.items():
            for z in ZGRID:
                for lo in range(1,15):
                    for hi in range(lo,15):configs.append(dict(profile=pro,z=z,lo=lo,hi=hi,assets=name,mask=mask,slope=0.,family='static'))
                for slope in [-.5,-.25,.25,.5]:
                    for lo,hi in [(1,14),(11,14),(3,10),(1,2),(1,10),(3,14)]:configs.append(dict(profile=pro,z=z,lo=lo,hi=hi,assets=name,mask=mask,slope=slope,family='slope'))
    for i,c in enumerate(configs):c['id']=i
    return configs

def cargs(c):return [c[k] for k in ['profile','z','lo','hi','mask','slope']]
def selected_rows(records,a,c=None,stress=.01,capcost=False,third_n=5,centfee=False,miss_rate=0.,miss_seed=0):
    args=cargs(c) if c is not None else [-1,99.,1,14,15,0.]
    ix=simulate(a,*args,stress,capcost,third_n,centfee,miss_rate,miss_seed);out=[]
    for i in ix:
        r=dict(records[int(a[i,OID])],lane=['current_signal95','premium_z3','third'][int(a[i,LANE])]);n=third_n if r['lane']=='third' else 5
        price=r['cap'] if capcost else r['entry'];paid=r['fee']
        if capcost or n!=5 or centfee:paid=fee(price,n,.01 if centfee else .0001)
        r.update(entry=price,fee=paid,quantity=n,pnl=n*(r['won']-price)-paid,pnl_cap=n*(r['won']-r['cap'])-fee(r['cap'],n));out.append(r)
    return sorted(out,key=lambda r:(r['close'],r['asset']))

def subset(a,period):
    i=PERIODS.index(period);return a[(a[:,CLOSE]>=BOUNDS[i])&(a[:,CLOSE]<BOUNDS[i+1])]

def screen():
    markets=pickle.loads((P/'features.pickle').read_bytes()) if (P/'features.pickle').exists() else build()
    records,a,counts=opportunities(markets)
    (P/'quote_opportunities.pickle').write_bytes(pickle.dumps((records,a),protocol=5))
    configs=configlist();save('configurations.json',configs)
    cm=np.array([cargs(c) for c in configs],dtype=float);results={};base={}
    for period in PERIODS[:2]:
        aa=subset(a,period);base[period]=list(faststats(aa,simulate(aa)))
        results[period]=batch(aa,cm);np.save(P/(period+'_screen.npy'),results[period])
        print(period,'baseline',base[period],'screened',len(configs),flush=True)
    save('screen_baselines.json',base)
    # This function never evaluates the later two periods.
    dev,val=results['development'],results['validation'];bd,bv=base.values()
    dn=dev[:,0]-bd[0];vn=val[:,0]-bv[0];dp=dev[:,2]-bd[2];vp=val[:,2]-bv[2]
    lookup={tuple(cargs(c)):i for i,c in enumerate(configs)}
    ranked=[]
    for i,c in enumerate(configs):
        peers=[]
        for dz,dl,dh in [(-.25,0,0),(.25,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1)]:
            key=tuple([c['profile'],round(c['z']+dz,2),c['lo']+dl,c['hi']+dh,c['mask'],c['slope']]);j=lookup.get(key)
            if j is not None:peers.append(j)
        stable=sum(dp[j]>0 and vp[j]>0 for j in peers)/len(peers) if peers else 0.
        eligible=dp[i]>0 and vp[i]>0 and dn[i]>=5 and vn[i]>=5 and dn[i]+vn[i]>=20 and stable>=.5 and dev[i,3]<=bd[3]+10 and val[i,3]<=bv[3]+10
        score=min(dp[i],vp[i])+.25*(dp[i]+vp[i]) if eligible else -1e6
        ranked.append(dict(c,development_delta_net=round(dp[i],5),validation_delta_net=round(vp[i],5),development_extra=int(dn[i]),validation_extra=int(vn[i]),neighbor_positive_fraction=stable,eligible=bool(eligible),score=round(score,5)))
    ranked.sort(key=lambda c:c['score'],reverse=True);save('ranking_selection_only.json',ranked)
    # Diverse primary leaders, plus requested z2/z3 time controls and broad-time leaders.
    chosen=[];groups=set()
    for c in ranked:
        if not c['eligible']:continue
        timefamily='late' if c['hi']<=2 else 'early' if c['lo']>=11 else 'middle' if c['lo']>=3 and c['hi']<=10 else 'mixed'
        key=(c['assets'],c['profile'],c['family'],timefamily)
        if key in groups:continue
        groups.add(key);chosen.append(dict(c,selection_reason='training/validation leader in distinct asset/profile/time family'))
        if len(chosen)>=12:break
    for pro in range(2):
        for z in [2.,3.]:
            for lo,hi in [(1,2),(11,14),(1,14),(3,10)]:
                i=lookup[(pro,z,lo,hi,15,0.)];c=next(c for c in ranked if c['id']==i)
                chosen.append(dict(c,selection_reason='prespecified all-asset z2/z3 time control'))
    # Add strongest BTC-only, ETH-only and early-window rules even if ineligible.
    for label,cond in [('BTC-only',lambda c:c['assets']=='BTC'),('ETH-only',lambda c:c['assets']=='ETH'),('early',lambda c:c['lo']>=11)]:
        pool=[c for c in ranked if cond(c)]
        c=max(pool,key=lambda c:min(c['development_delta_net'],c['validation_delta_net']))
        chosen.append(dict(c,selection_reason=label+' development/validation comparator'))
    chosen=list({c['id']:c for c in chosen}.values())
    save('shortlist_frozen.json',dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),plan_sha256=digest(P/'PLAN.md'),configurations_sha256=digest(P/'configurations.json'),static_trials=21420,slope_trials=len(configs)-21420,eligible_count=sum(c['eligible'] for c in ranked),selection_periods=list(PERIODS[:2]),later_results_not_evaluated=True,candidates=chosen))
    print('eligible',sum(c['eligible'] for c in ranked),'frozen',len(chosen),flush=True)
    for c in chosen[:12]:print({k:c[k] for k in ['id','assets','profile','z','lo','hi','slope','development_extra','validation_extra','development_delta_net','validation_delta_net','neighbor_positive_fraction']},flush=True)

if __name__=='__main__':screen()
