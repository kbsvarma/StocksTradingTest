import run as r
from run import *
r.ENTRIES=['momentum72','break168','momentum12align168'];r.EXITS=['target3R','delayed_trail1R','wide2R','wide3R']
P0=P;P=P/'followup';P.mkdir(exist_ok=True);(P/'ledgers').mkdir(exist_ok=True);r.P=P
def signals(close,crypto=True):
 lp=np.log(close);v=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);stop=np.maximum(.005 if crypto else .002,2*v);r72=lp-lp.shift(72);r12=lp-lp.shift(12);r168=lp-lp.shift(168)
 ss=[np.sign(r72).where(abs(r72)>v,0),pd.Series(np.where(lp>lp.rolling(168).max().shift(1),1,np.where(lp<lp.rolling(168).min().shift(1),-1,0)),index=lp.index),np.sign(r12).where((abs(r12)>v)&(np.sign(r12)==np.sign(r168)),0)]
 out=[]
 for side in ss:
  side=side.where((stop<=.12)&stop.notna(),0)
  if not crypto:
   dates=pd.to_datetime(close.index,unit='s',utc=True).tz_convert('America/New_York');mins=dates.hour*60+dates.minute;side=side.where((dates.weekday<5)&(mins>=570)&(mins<=900),0)
  out.append(np.c_[side.fillna(0),stop.fillna(0),np.full(len(side),np.nan),np.full(len(side),np.nan)])
 return out
rows=[]
for asset in [x['ticker'] for x in json.load(open(prior.P/'coverage.json'))]+['KXETHPERP','KXXRPPERP','KXBNBPERP','KXHYPEPERP','KXZECPERP']:
 ts,A,F,_=prior.data_load(asset);crypto=prior.r.old.M[asset]['asset_class']=='Crypto';mid=pd.Series(A[:,:2].mean(axis=1),index=ts);q=mid[mid.index%3600==0];big=[]
 for small in signals(q,crypto):
  S=np.zeros((len(ts),4));S[:,0]=99;S[:,2:]=np.nan;S[np.searchsorted(ts,q.index)]=small;big.append(S)
 cut=np.searchsorted(ts,int(pd.Timestamp('2026-09-01' if crypto else '2026-10-01',tz='UTC').timestamp()));en=min(len(ts),np.searchsorted(ts,int(pd.Timestamp('2026-10-10',tz='UTC').timestamp())))
 rows+=execute(asset,'venue',ts,A,F,big,float(prior.r.fees['taker_fee_rates'][asset]),[('early',0,min(cut,en)),('later',min(cut,en),en)],[('base',.0001,1,0,0),('cost5',.0005,1,0,0),('delay2',.0001,2,0,0),('combined',.0005,2,1,0)])
 print(asset,flush=True)
pd.DataFrame(rows).to_csv(P/'venue_results.csv',index=False);rows=[]
for asset in ['BTC','ETH','SOL','ZEC']:
 raw=json.load(gzip.open(P0/'data'/f'{asset}.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']).set_index('t').sort_index();ts=np.arange(d.index.min(),d.index.max()+3600,3600);d=d.reindex(ts);unit=float(prior.r.old.M['KX'+asset+'PERP']['contract_size']);A=d[['open','open','low','high','high','low','volume','close']].to_numpy();A[:,:6]*=unit;A[:,7]*=unit
 periods=[(str(y),np.searchsorted(ts,int(pd.Timestamp(f'{y}-01-01',tz='UTC').timestamp())),np.searchsorted(ts,int(pd.Timestamp(f'{y+1}-01-01',tz='UTC').timestamp()))) for y in range(2022,2026)]+[('all',0,len(ts))]
 rows+=execute(asset,'proxy',ts,A,np.zeros(len(ts)),signals(pd.Series(d.close.to_numpy()*unit,index=ts)),.0012,periods,[('base',.0001,1,0,.0001),('combined',.0005,2,1,.0003),('no_funding',.0001,1,0,0)])
 print(asset,flush=True)
pd.DataFrame(rows).to_csv(P/'proxy_results.csv',index=False)
