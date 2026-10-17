from pathlib import Path
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent;B=P.parent
sel=pd.read_csv(B/'phase2/selection.csv');names=sorted(set(sel[sel.month=='2024-12'].symbol)|{'SPY'})
spy=pd.read_parquet(B/'data/SPY.parquet');calendar=pd.to_datetime(spy.index.date);dates=calendar[(calendar>='2022-01-01')&(calendar<='2026-08-03')];rows=[]
arc=pd.read_parquet(B/'phase2/archive_opens.parquet');arc=arc[(arc.minute==575)&(arc.volume>0)].set_index(['exit','symbol'])['open']
for symbol in names:
 f=B/'data'/f'{dict(FB="META",SQ="XYZ",ANTM="ELV").get(symbol,symbol)}.parquet'
 if not f.exists():continue
 b=pd.read_parquet(f);b.index=pd.to_datetime(b.index.date);b.loc[b.Volume.le(0),['Open','Close']]=np.nan;b=b.reindex(calendar);divs=b.Dividends.fillna(0)
 split=b['Stock Splits'].replace(0,1).fillna(1);fac=split.iloc[::-1].cumprod().iloc[::-1]/split
 for i,entry in enumerate(calendar[:-1]):
  if entry<pd.Timestamp('2022-01-01') or entry>pd.Timestamp('2026-07-31'):continue
  ex=calendar[i+1];close=b.Close.loc[entry];enfactor=fac.loc[entry];exfactor=fac.loc[ex]
  if not np.isfinite(close):continue
  nominal=close*enfactor;qty=np.floor(9995/nominal)
  if qty<=0:continue
  notional=qty*nominal;div=divs.loc[ex]*enfactor*qty;exitprice=b.Open.loc[ex]*exfactor;sellqty=qty*enfactor/exfactor
  missing=not np.isfinite(exitprice);exitprice=0 if missing else exitprice
  buyfee=min(max(qty*.005,1),notional*.01)
  def pnl(price):
   proceeds=sellqty*price;sellfee=min(max(sellqty*.005,1),proceeds*.01)
   fees=buyfee+sellfee+proceeds*.0000206+min(sellqty*.000195,9.79)+(qty+sellqty)*.000003
   return proceeds+div-notional-fees-notional*.0003
  late=arc.get((ex,symbol),np.nan)
  rows.append(dict(symbol=symbol,entry=entry,exit=ex,net=pnl(exitprice),gross=qty*(b.Open.loc[ex]+divs.loc[ex]-close)*enfactor if not missing else -notional,notional=notional,late_net=pnl(late) if np.isfinite(late) else np.nan,missing_exit=missing))
t=pd.DataFrame(rows);t.to_parquet(P/'stock_nights.parquet',index=False)
train=t[t.exit<'2025-01-01'].groupby('symbol').agg(train_n=('net','size'),train_net_mean=('net','mean'))
rank=train[(train.train_n>=600)&(train.index!='SPY')].sort_values('train_net_mean',ascending=False);selected=rank.head(10).index.tolist();rank.to_csv(P/'training_ranking.csv')
rows=[]
for symbol in selected+['SPY']:
 g=t[(t.symbol==symbol)&(t.entry>='2025-01-01')];matched=g[g.late_net.notna()]
 rows.append(dict(symbol=symbol,train_n=int(train.loc[symbol,'train_n']),train_mean_bps=train.loc[symbol,'train_net_mean'],forward_n=len(g),forward_mean_bps=g.net.mean(),total_net_10k=g.net.sum(),win_pct=(g.net>0).mean()*100,worst_night_10k=g.net.min(),matched_n=len(matched),matched_open_mean_bps=matched.net.mean(),matched_0935_mean_bps=matched.late_net.mean(),missing_exits=int(g.missing_exit.sum())))
out=pd.DataFrame(rows);out.to_csv(P/'frozen10.csv',index=False)
y=t[t.symbol.isin(selected)].groupby(['symbol',t.entry.dt.year]).agg(n=('net','size'),mean_bps=('net','mean'),net_10k=('net','sum'));y.to_csv(P/'annual_stock.csv')
# Honest hindsight list, separate from chronological selection.
h=t[t.entry>='2025-01-01'].groupby('symbol').agg(n=('net','size'),mean_bps=('net','mean'));h=h[h.n>=300].sort_values('mean_bps',ascending=False);h.to_csv(P/'hindsight_2025.csv')
print(out.to_string(index=False));print('Annual');print(y.to_string());print('hindsight');print(h.head(10).to_string())
