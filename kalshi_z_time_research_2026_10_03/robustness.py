"""Post-screen diagnostics, no new parameter selection or live changes."""
from evaluate import compare
from study import *
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    configs=json.loads((P/'configurations.json').read_text());byid={c['id']:c for c in configs}
    records,a=pickle.loads((P/'quote_opportunities.pickle').read_bytes());base=selected_rows(records,a);out={}
    lookup={tuple(cargs(c)):c for c in configs}
    raw={co:np.load(P/(co+'_screen.npy' if co!='later' else 'later_descriptive_screen.npy')) for co in PERIODS[:3]}
    baselines=json.loads((P/'screen_baselines.json').read_text());baselines['later']=list(faststats(subset(a,'later'),simulate(subset(a,'later'))))
    for cid in [25301,25169,9220,7214,11752,25426]:
        c=byid[cid];joint=selected_rows(records,a,c);thirdonly=selected_rows(records,a[a[:,LANE]==2],c)
        neighbors=[]
        for dz,dl,dh in [(-.25,0,0),(.25,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1)]:
            key=(c['profile'],c['z']+dz,c['lo']+dl,c['hi']+dh,c['mask'],c['slope']);n=lookup.get(key)
            if n is None:continue
            neighbors.append(dict(rule=n,periods={co:dict(extra_trades=int(raw[co][n['id'],0]-baselines[co][0]),delta_net=round(float(raw[co][n['id'],2]-baselines[co][2]),5)) for co in PERIODS[:3]}))
        out[str(cid)]=dict(config=c,continuous51days=compare(joint,base),standalone51days=old.metrics(thirdonly),neighbors=neighbors)
        # Count every first qualifying opportunity, independent of account risk.
        potential=[];seen=set()
        for r in a:
            if r[LANE]!=2 or int(r[MID]) in seen or int(r[PROFILE])!=c['profile'] or not ((1<<int(r[AID]))&c['mask']) or not c['lo']<=r[REM]<=c['hi'] or r[Z]<max(.5,c['z']+c['slope']*math.log2(r[REM]/5)):continue
            seen.add(int(r[MID]));potential.append(records[int(r[OID])])
        out[str(cid)]['first_opportunity_no_risk_caps_51days']=old.metrics(potential)
        out[str(cid)]['standalone_by_period']={co:old.metrics(selected_rows(records,subset(a,co)[subset(a,co)[:,LANE]==2],c)) for co in PERIODS}
    save('robustness.json',dict(continuous_baseline51days=old.metrics(base),candidates=out))
    table=[]
    for assets in ['BTC','ETH','SOL','XRP','BTC_ETH','ALL']:
        for profile in range(2):
            for z in ZGRID:
                for rem in range(1,15):
                    c=lookup[(profile,z,rem,rem,ASSETSETS[assets],0.)]
                    table.append(dict(assets=assets,profile=profile,z=z,remaining=rem,**{co:dict(extra_trades=int(raw[co][c['id'],0]-baselines[co][0]),delta_net=round(float(raw[co][c['id'],2]-baselines[co][2]),5)) for co in PERIODS[:3]}))
    save('minute_surface.json',table)
    fig,axes=plt.subplots(2,2,figsize=(14,10),layout='constrained')
    for ax,(assets,profile) in zip(axes.flat,[('BTC_ETH',0),('ALL',0),('BTC_ETH',1),('ALL',1)]):
        zz=np.array([[next(r for r in table if r['assets']==assets and r['profile']==profile and r['z']==z and r['remaining']==rem)['later']['delta_net'] for rem in range(1,15)] for z in ZGRID])
        im=ax.imshow(zz,cmap='RdYlGn',vmin=-25,vmax=25,aspect='auto',origin='lower')
        ax.set(xticks=range(14),xticklabels=range(1,15),yticks=range(len(ZGRID)),yticklabels=ZGRID,xlabel='Minutes remaining (one-minute entry band)',ylabel='Minimum z',title=assets.replace('_',' + ')+(' | 85–<95¢' if profile==0 else ' | 93–<98¢'))
    fig.colorbar(im,ax=axes,label='Incremental modeled net P&L ($), clipped ±25')
    fig.suptitle('z and time jointly change the result\nSep 12–Oct 1 · third lane size5 · shared risk · fees +1¢/contract · quote proxy',fontsize=14)
    fig.savefig(P/'z_time_surface.png',dpi=160);fig.savefig(P/'z_time_surface.pdf');plt.close(fig)
    print('Saved robustness, minute surfaces and charts',flush=True)

if __name__=='__main__':main()
