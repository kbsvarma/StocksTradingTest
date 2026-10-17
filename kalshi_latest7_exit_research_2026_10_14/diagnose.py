from pathlib import Path
import sys,json,gzip,pickle,datetime
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
sys.path.insert(0,str(R/'kalshi_timed_z_exits_2026_10_14'))
import grid as g
import details as dt
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def main():
 d=pickle.load(gzip.open(g.PREV/'inputs.pkl.gz','rb'));ss=pickle.load(gzip.open(g.PREV/'seconds.pkl.gz','rb'));rr=d['actual']
 cc=json.loads((g.P/'configs.json').read_text());arr=dict(np.load(g.P/'grid_arrays.npz'));sel=json.loads((g.P/'selection.json').read_text());old={a:v['config'] for a,v in sel['asset_specific']['0']['choices'].items()}
 out=[]
 for r in rr:
  if r['won'] or r['close']<g.SPLIT:continue
  p=g.prepare(r,d,ss);c=old[r['asset']];x=dict(r);x['old_rule']=c;x['old_exit']=dt.record(r,c,p)
  x['minimum_z']=float(p['z'].min());x['minimum_time_remaining']=int(p['remaining'][p['z'].argmin()]);x['segments']={}
  for hi,lo in [(900,300),(300,180),(180,120),(120,60),(60,30),(30,10),(10,0)]:
   ix=(p['remaining']<=hi)&(p['remaining']>lo)
   if ix.any():x['segments'][f'{hi}-{lo}']=dict(min_z=float(p['z'][ix].min()),max_z=float(p['z'][ix].max()),last_z=float(p['z'][ix][-1]),bid_low_min=float(np.nanmin(p['low']['price'][ix])),bid_high_max=float(np.nanmax(p['high']['price'][ix])))
  x['crossings']={}
  for lev in [-1,-.5,0,.25,.5,.75,1,1.5,2,2.5,3]:
   q=dict(id='diagnostic',family='absolute',level=lev,grace=0,persist=0,window='all');v=dt.record(r,q,p)
   if v['trigger'] is not None:v['age']=v['trigger']-r['entered'];v['remaining']=r['close']-v['trigger']
   x['crossings'][str(lev)]={k:v.get(k) for k in ['trigger','age','remaining','trigger_z','exit','price','delta','net']}
  x['old_level_without_time']=dt.record(r,dict(c,grace=0,persist=0,window='all'),p)
  out.append(x)
 save('seven_diagnoses.json',out)
 # Reuse the broad existing grid: later-period opportunity subject to full-history cuts.
 won=np.array([bool(r['won']) for r in rr]);late=np.array([r['close']>=g.SPLIT for r in rr]);hold=np.array([r['hold_net'] for r in rr]);delta=arr['net_low']-hold
 result={}
 for a in ['ALL','BTC','WTI','NATGAS','USDCAD','FX']:
  mask=np.array([a=='ALL' or r['asset']==a or (a=='FX' and r['asset'] in ['EURUSD','USDCAD']) for r in rr]);ml=mask&late;me=mask&~late
  wc=(arr['fill_low'][:,mask&won]).sum(axis=1);change=delta[:,ml].sum(axis=1);early=delta[:,me].sum(axis=1);cuts=(arr['fill_low'][:,ml&won]).sum(axis=1)
  result[a]={}
  for budget in [0,1,2,4,8,16]:
   ok=[k for k,c in enumerate(cc) if c['family']!='floors' and wc[k]<=budget]
   k=max(ok,key=lambda k:(change[k],early[k],-wc[k],-k))
   result[a][str(budget)]=dict(config=cc[k],index=k,full=g.metric(rr,arr['net_low'][k],arr['fill_low'][k],arr['fee_low'][k],arr['friction_low'][k],mask,arr['trigger'][k]),late=g.metric(rr,arr['net_low'][k],arr['fill_low'][k],arr['fee_low'][k],arr['friction_low'][k],ml,arr['trigger'][k]),early=g.metric(rr,arr['net_low'][k],arr['fill_low'][k],arr['fee_low'][k],arr['friction_low'][k],me,arr['trigger'][k]))
 save('existing_grid_frontier.json',result)
 print('SEVEN')
 for x in out:print(x['ticker'], 'entryz',x['entry_z'],'holdsec',x['close']-x['entered'],'minz',x['minimum_z'],'minrem',x['minimum_time_remaining'],'oldraw',x['old_level_without_time']['trigger'])
 print('FRONTIER')
 for a,vs in result.items():
  for b,v in vs.items():print(a,b,v['config']['id'],'late',round(v['late']['delta'],4),'early',round(v['early']['delta'],4),'cuts',v['full']['winners_cut'],'save',round(v['late']['loss_savings'],4))
if __name__=='__main__':main()
