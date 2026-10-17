from common import *
ROOT=P.parent;WIDTH=2.
def main():
 u=pd.read_parquet(ROOT/'trend_replication_2026_07/data/etf/IWM_1m.parquet');et=u.index.tz_convert('America/New_York');u['day']=et.strftime('%Y-%m-%d');u['minute']=et.hour*60+et.minute;under={}
 for m,name,col in [(570,'open','open'),(599,'signal_price','close')]:under[name]=u[u.minute==m].set_index('day')[col]
 under=pd.DataFrame(under);results=[];audit=[]
 for i,file in enumerate(sorted((ROOT/'spx_spread_bot/backtests/multi_ic_2026/data/iwm_wide').glob('*.parquet'))):
  day=file.stem[-8:];day=str(pd.Timestamp(day).date())
  if day not in under.index or under.loc[day].isna().any():audit.append(dict(day=day,status='missing_underlying'));continue
  q=pd.read_parquet(file);et=q.index.tz_convert('America/New_York');minute=et.hour*60+et.minute;q=q.assign(minute=minute);entry=q[q.minute==601].copy();exitq=q[q.minute==765].copy();parsed=entry.symbol.str.extract(r'^IWM\s+(\d{6})([CP])(\d{8})$');entry['expiry']=pd.to_datetime(parsed[0],format='%y%m%d',errors='coerce');entry['right']=parsed[1];entry['strike']=pd.to_numeric(parsed[2],errors='coerce')/1000;dte=(entry.expiry-pd.Timestamp(day)).dt.days;entry=entry[dte.between(1,7)&entry.bid_px_00.ge(0)&entry.ask_px_00.gt(0)&entry.ask_px_00.ge(entry.bid_px_00)];entry=entry.drop_duplicates('symbol',keep='last')
  if entry.empty:audit.append(dict(day=day,status='no_eligible_entry_quotes'));continue
  entry=entry[entry.expiry==entry.expiry.min()];calls=entry[entry.right=='C'].set_index('strike');puts=entry[entry.right=='P'].set_index('strike');common=calls.index.intersection(puts.index);available=[k for k in common if k+WIDTH in calls.index and k-WIDTH in puts.index]
  if not available:audit.append(dict(day=day,status='missing_wings'));continue
  spot=under.loc[day,'signal_price'];k=min(available,key=lambda x:abs(x-spot));legs=[calls.loc[k],calls.loc[k+WIDTH],puts.loc[k],puts.loc[k-WIDTH]];exitq=exitq.drop_duplicates('symbol',keep='last').set_index('symbol');missing=0;values={}
  for side,a,b in [('call',legs[0],legs[1]),('put',legs[2],legs[3])]:
   debit=a.ask_px_00-b.bid_px_00;credit=a.bid_px_00-b.ask_px_00
   if a.symbol in exitq.index and b.symbol in exitq.index:
    aa=exitq.loc[a.symbol];bb=exitq.loc[b.symbol];valid=np.isfinite([aa.bid_px_00,aa.ask_px_00,bb.bid_px_00,bb.ask_px_00]).all() and aa.ask_px_00>=aa.bid_px_00>=0 and bb.ask_px_00>=bb.bid_px_00>=0
   else:valid=False
   if valid:long_exit=aa.bid_px_00-bb.ask_px_00;short_exit=aa.ask_px_00-bb.bid_px_00
   else:long_exit=0.;short_exit=WIDTH;missing+=1
   values['debit_'+side]=(long_exit-debit)*100 if 0<debit<=WIDTH else np.nan;values['credit_'+side]=(credit-short_exit)*100 if 0<credit<=WIDTH else np.nan
  up=spot>=under.loc[day,'open'];designs={**{s:(v,4) for s,v in values.items()},'buy_winged_straddle':(values['debit_call']+values['debit_put'],8),'sell_winged_straddle':(values['credit_call']+values['credit_put'],8),'opening_follow_debit':(values['debit_call' if up else 'debit_put'],4),'opening_fade_debit':(values['debit_put' if up else 'debit_call'],4),'opening_follow_credit':(values['credit_put' if up else 'credit_call'],4),'opening_fade_credit':(values['credit_call' if up else 'credit_put'],4)}
  for case in ['base','stress']:
   for name,(gross,fills) in designs.items():
    if not np.isfinite(gross):continue
    fee=fills*(.65 if case=='base' else 1.30);slip=fills*(0. if case=='base' else 1.);results.append(dict(date=pd.Timestamp(day,tz='America/New_York'),case=case,design=name,gross=gross,fees=fee,slippage=slip,net=gross-fee-slip,activity=1.,strike=k,expiry=str(entry.expiry.iloc[0].date()),missing_exit=missing))
  audit.append(dict(day=day,status='evaluated',missing_exit=missing,entry_symbols=len(entry),expiry=str(entry.expiry.iloc[0].date())))
  if i%200==0:print('Option dates',i,flush=True)
 a=pd.DataFrame(results);a.to_parquet(O/'option_daily.parquet');pd.DataFrame(audit).to_csv(O/'option_coverage.csv',index=False);rows=[]
 for (case,name),g in a.groupby(['case','design']):
  z=g.set_index('date').sort_index()
  for period,r in partitions(z).items():rows.append(dict(case=case,design=name,period=period,**r))
 pd.DataFrame(rows).to_csv(O/'option_scoreboard.csv',index=False);print('Option replay complete',len(a),flush=True)
if __name__=='__main__':main()
