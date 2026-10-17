from run import *
ASSETS=['BTC','ETH','SOL','ZEC'];Q=P/'portfolio';Q.mkdir(exist_ok=True)
def build(source,stress):
 closes=[];fund=[];quotes=[]
 for asset in ASSETS:
  unit=float(prior.r.old.M['KX'+asset+'PERP']['contract_size'])
  if source=='proxy':
   raw=json.load(gzip.open(P/'data'/f'{asset}.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']);d.index=pd.to_datetime(d.t,unit='s',utc=True)
   c=(d.close*unit).resample('1D').last();hour='01:00' if stress else '00:00';op=(d.open*unit).between_time(hour,hour);op.index=op.index.floor('D');bid=ask=op;f=pd.Series(0.,index=c.index)
  else:
   ts,A,F,_=prior.data_load('KX'+asset+'PERP');d=pd.DataFrame({'bid':A[:,0],'ask':A[:,1],'fund':F},index=pd.to_datetime(ts,unit='s',utc=True));m=(d.bid+d.ask)/2
   c=m.between_time('23:00','23:59').resample('1D').last();hour='01:00' if stress else '00:00';op=d.between_time(hour,hour).copy();op.index=op.index.floor('D');bid=op.bid;ask=op.ask;f=op.fund
  closes.append(c.rename(asset));quotes.append(pd.concat([bid.rename(asset+'_b'),ask.rename(asset+'_a')],axis=1));fund.append(f.rename(asset))
 C=pd.concat(closes,axis=1).sort_index();idx=pd.date_range(C.index.min(),C.index.max(),freq='D',tz='UTC');return C.reindex(idx),pd.concat(quotes,axis=1).reindex(idx),pd.concat(fund,axis=1).reindex(idx)
def simulate(C,O,F,signal,source,stress,st,en,chosen,fundscale=1.):
 cash=100.;qty=np.zeros(4);last=np.full(4,np.nan);lastf=np.zeros(4);peak=100.;dd=0.;turn=0.;fees=0.;fund=0.;n=0;missing=0;path=[];pnls=np.zeros(4);fr=.0005 if stress else .0001;fee=.0012
 B=O.iloc[:,::2].to_numpy();A=O.iloc[:,1::2].to_numpy();FF=F.to_numpy()
 for i in range(st,en):
  bid=B[i];ask=A[i];mid=(bid+ask)/2;valid=np.isfinite(mid);daily=np.zeros(4)
  for j in range(4):
   if qty[j]!=0:
    if valid[j] and np.isfinite(last[j]):daily[j]+=qty[j]*(mid[j]-last[j])
    if not valid[j]:missing+=1
    if source=='proxy':charge=abs(qty[j])*(mid[j] if valid[j] else last[j])*(.0003 if stress else .0001)*3
    else:charge=qty[j]*(FF[i,j]-lastf[j]) if np.isfinite(FF[i,j]) else 0.
    charge*=fundscale;daily[j]-=charge;fund-=charge
   if valid[j]:last[j]=mid[j]
   if np.isfinite(FF[i,j]):lastf[j]=FF[i,j]
  cash+=daily.sum();pnls+=daily;target=np.zeros(4)
  for j in chosen:
   if valid[j]:target[j]=np.sign(signal[i,j])*int(abs(signal[i,j])*.5*max(0,cash)/len(chosen)/mid[j]) if i<en-1 else 0
   else:target[j]=qty[j]
  for j in chosen:
   if not valid[j]:continue
   delta=target[j]-qty[j]
   if delta:
    px=ask[j] if delta>0 else bid[j];cost=abs(delta)*(px*(fee+fr)+abs(px-mid[j]));cash-=cost;pnls[j]-=cost;fees+=cost;turn+=abs(delta)*px;n+=1
   qty[j]=target[j]
  peak=max(peak,cash);dd=max(dd,peak-cash);path.append(cash)
 remaining=abs(qty).sum()
 if remaining:
  cost=(abs(qty)*np.nan_to_num(last)*(fee+fr)).sum();cash-=cost;fees+=cost;pnls-=abs(qty)*np.nan_to_num(last)*(fee+fr);path[-1]=cash;dd=max(dd,peak-cash)
 assert abs(pnls.sum()-(cash-100))<1e-7
 return dict(net=cash-100,dd=dd,orders=n,turnover=turn,fees_friction=fees,funding=fund,missing_held=missing,stale_terminal=bool(remaining)),path,pnls
def main():
 rows=[]
 for source in ['proxy','venue']:
  for stress in [False,True]:
   C,O,F=build(source,stress);lp=np.log(C);rules={f'mom{h}':np.sign(lp-lp.shift(h)) for h in [7,30,90]};rules['ema7_28']=np.sign(lp.ewm(span=7,adjust=False,min_periods=28).mean()-lp.ewm(span=28,adjust=False,min_periods=28).mean())
   periods=[(str(y),f'{y}-01-01',f'{y+1}-01-01') for y in range(2022,2026)]+[('all','2022-01-01','2026-01-01')] if source=='proxy' else [('early','2026-06-01','2026-09-01'),('later','2026-09-01','2026-10-10'),('all','2026-06-01','2026-10-10')]
   for name,sig in rules.items():
    for direction in ['long_flat','long_short']:
     signals=sig.clip(lower=0) if direction=='long_flat' else sig;S=signals.shift(1).fillna(0).to_numpy()
     for label,start,end in periods:
      st=C.index.searchsorted(pd.Timestamp(start,tz='UTC'));en=C.index.searchsorted(pd.Timestamp(end,tz='UTC'))
      if en<=st:continue
      for basket,chosen in [('basket',range(4))]+[(asset,[j]) for j,asset in enumerate(ASSETS)]:
       result,path,pnl=simulate(C,O,F,S,source,stress,st,en,list(chosen));result.update(source=source,scenario='combined' if stress else 'base',entry=name,direction=direction,period=label,basket=basket,days=en-st);rows.append(result)
       if basket=='basket':np.savez_compressed(Q/f'{source}_{name}_{direction}_{label}_{result["scenario"]}.npz',equity=path,asset_pnl=pnl,dates=C.index[st:en].asi8)
  pd.DataFrame(rows).to_csv(Q/'results.csv',index=False);print(source,'done',flush=True)

if __name__=='__main__':main()
