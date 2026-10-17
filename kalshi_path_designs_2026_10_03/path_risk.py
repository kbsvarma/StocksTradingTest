"""Isolated prior risk model; use exact candidate-size book fees from inputs."""
from study import *

@njit(cache=True)
def simulate_exact(a,profile=-1,zmin=99.,lo=1,hi=14,mask=15,slope=0.,stress=.01,capcost=False,third_n=5,centfee=False,miss_rate=0.,miss_seed=0):
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
        # FEE is rebuilt for the intended quantity before this replay; preserve multi-level fees.
        paid=r[FEE]
        if capcost or centfee:
            unit=.01 if centfee else .0001
            paid=math.ceil((n*price+.07*n*price*(1-price)-1e-10)/unit)*unit-n*price
        cost=n*price+paid+stress*n
        cash-=cost
        for k in range(20):
            if active[k]<0:active[k]=j;qty[k]=n;pnl[k]=n*r[WON]-cost;break
        seen[int(r[MID])]=True;accepted[nacc]=j;nacc+=1
    return accepted[:nacc]
