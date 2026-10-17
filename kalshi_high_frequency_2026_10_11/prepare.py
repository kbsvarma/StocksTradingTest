"""Read-only frozen observer audit and paired-research input preparation."""
from pathlib import Path
import sqlite3,json,math,pickle,sys,datetime as dt,collections,hashlib
import numpy as np
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_every_window_2026_10_03'))
import features
from paper import price
sys.path.insert(0,str(P))
import public
def ts(s):return int(dt.datetime.fromisoformat(s+'T00:00:00+00:00').timestamp())
def fee(p):return math.ceil(.07*p*(1-p)*10000-1e-9)/10000
def save(name,x):public.save(P/name,x)
def metric(rr):
 pp=np.array([r['pnl'] for r in rr]);eq=np.r_[0,np.cumsum(pp)];days=collections.defaultdict(float)
 for r in rr:days[dt.datetime.fromtimestamp(r['close'],dt.timezone.utc).date().isoformat()]+=r['pnl']
 return dict(n=len(rr),wins=sum((r['result']=='yes')==(r['side']==1) for r in rr),net=round(float(pp.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),daily=dict(days))
def run():
 db=sqlite3.connect('file:'+str(P/'forward_snapshot.sqlite3')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 meta=dict(db.execute('select * from meta').fetchall());started=float(meta['started']);firstclose=(int(started)//900+1)*900
 models=json.loads((P.parent/'kalshi_every_window_2026_10_03/models.json').read_text())
 assert hashlib.sha256((P.parent/'kalshi_every_window_2026_10_03/models.json').read_bytes()).hexdigest()==meta['model_hash']
 offers={};books={};fm={};errors=collections.Counter();evidence_assertions=0;all_settles={}
 for row in db.execute('select ts,kind,data from events order by id'):
  d=json.loads(row['data']);kind=row['kind']
  if kind=='decision':
   for o in d['offers']:
    offers[o['ticker'],d['T']]=o;f=o['feature'];assert abs(features.probability(f,models[o['asset']+'_rich'])-o['probability_yes'])<1e-10;evidence_assertions+=1
    m=fm.setdefault(o['ticker'],dict(ticker=o['ticker'],asset=o['asset'],start=d['close']-900,close=d['close'],new_features={},candles={}))
    m['new_features'][f['rem']]={**f,'prich':o['probability_yes'],'received':row['ts']}
   for a in d['raw']['candles']:
    m=fm.get(a.get('ticker'))
    if m is None:continue
    for c in a.get('raw_candles',{}).get('candlesticks',[]):
     t=int(c['end_period_ts']);q={'t':t,'v':c.get('volume_fp',c.get('volume',0))}
     for short,field in [('b','yes_bid'),('a','yes_ask')]:
      for sc,lf in [('o','open'),('c','close')]:q[short+sc]=c[field].get(lf+'_dollars',c[field].get(lf))
     m['candles'].setdefault(t,q)
     f=m['new_features'].get((m['close']-t)//60)
     if f is not None:f.setdefault('observed_volume',q['v'])
  elif kind=='book_execution':books[d['ticker'],d['T']]=d
  elif kind=='settlement':all_settles[d['ticker']]=d
  elif kind=='error':errors[d['type']]+=1
 rows=[]
 for row in db.execute("select * from trades where status='settled' order by entered"):
  r=dict(row)
  if r['close']<=firstclose:continue
  o=offers[r['ticker'],r['decision']];b=books[r['ticker'],r['decision']];f=o['feature'];fill=price(b['book'],r['side'])
  assert abs(fill['price']-r['entry'])<1e-9 and abs(fill['fee']-r['fee'])<1e-9
  payout=int((r['result']=='yes')==(r['side']==1));assert abs(payout-r['entry']-r['fee']-.0025-r['pnl'])<1e-8
  assert r['result']==all_settles[r['ticker']]['result']
  assert r['decision']<b['received_ts']<r['close'] and b['received_ts']-r['decision']<=35
  assert b['received_ts']-b['before_ts']>=1
  sig=f['a'] if r['side']==1 else 1-f['b'];prob=o['probability_yes'] if r['side']==1 else 1-o['probability_yes']
  r.update(signal_ask=sig,predicted=prob,slippage=r['entry']-sig,live_edge=prob-r['entry']-r['fee']-.0025)
  rows.append(r);evidence_assertions+=6
 summary={}
 for lane in ['force','skip']:
  rr=[r for r in rows if r['lane']==lane];q=metric(rr);q['net_at_1c']=round(q['net']-.0075*len(rr),6);q['net_at_2c']=round(q['net']-.0175*len(rr),6)
  q['signal_quote_counterfactual_net']=round(sum(int((r['result']=='yes')==(r['side']==1))-r['signal_ask']-fee(r['signal_ask'])-.0025 for r in rr),6)
  q['price_deterioration']=round(sum(r['slippage'] for r in rr),6);q['avg_predicted_win']=float(np.mean([r['predicted'] for r in rr]));q['actual_win_rate']=q['wins']/q['n'];q['by_asset']={a:metric([r for r in rr if r['asset']==a]) for a in ['BTC','ETH']}
  q['posthoc_same_sample_reprice_controls']={}
  for floor in [0,.01,.02,.03]:
   sub=[r for r in rr if r['live_edge']>=floor];q['posthoc_same_sample_reprice_controls'][str(floor)]=metric(sub)
  q['elapsed_calendar_days']=(max(r['close'] for r in rr)-firstclose)/86400
  q['entries_per_calendar_day']=len(rr)/q['elapsed_calendar_days']
  q['complete_week_oct4_10']=metric([r for r in rr if ts('2026-10-04')<=r['close']<ts('2026-10-11')])
  q['complete_week_oct4_10']['net_at_1c']=q['complete_week_oct4_10']['net']-.0075*q['complete_week_oct4_10']['n']
  summary[lane]=q
 save('observer_audit.json',dict(started=started,first_partial_close_excluded=firstclose,meta=meta,errors=dict(errors),assertions=evidence_assertions,stats=summary,rows=len(rows)))
 save('observer_trades.json.gz',rows)
 print(json.dumps(summary,indent=2),flush=True)
 # Public settled metadata for both sides, plus full quote arrays. Original
 # observed signals remain from the frozen stream, never backfilled features.
 start=ts('2026-10-04');end=ts('2026-10-11')
 metas={}
 for asset in ['BTC','ETH']:
  mm=public.paged('/markets',dict(series_ticker='KX'+asset+'15M',status='settled',min_close_ts=start,max_close_ts=end,limit=1000),'markets')
  for m in mm:
   if start<=public.ts(m['close_time'])<end:metas[m['ticker']]=m
 save('forward_markets.json',metas)
 want=[t for t,m in fm.items() if start<=m['close']<end and t in metas]
 missing=[t for t,m in fm.items() if start<=m['close']<end and t not in metas]
 assert not missing,missing[:5]
 for i in range(0,len(want),20):
  tt=want[i:i+20];lo=min(fm[t]['start'] for t in tt);hi=max(fm[t]['close'] for t in tt)
  raw=public.get('/markets/candlesticks',dict(market_tickers=','.join(tt),start_ts=lo,end_ts=hi,period_interval=1))
  for item in raw['markets']:
   m=fm[item.get('market_ticker',item.get('ticker'))]
   for c in item['candlesticks']:
    t=int(c['end_period_ts']);q={'t':t,'v':c.get('volume_fp',c.get('volume',0))}
    for short,field in [('b','yes_bid'),('a','yes_ask')]:
     for sc,lf in [('o','open'),('c','close')]:q[short+sc]=c[field].get(lf+'_dollars',c[field].get(lf))
    m['candles'][t]=q
  print('quotes',i+len(tt),'/',len(want),flush=True)
 out=[]
 for t in want:
  m=fm[t];source=metas[t];assert source['result'] in ['yes','no'];m['result']=1 if source['result']=='yes' else -1;m['settled']=public.ts(source['settlement_ts']);m['cohort']='forward';out.append(m)
 (P/'forward_prepared.pickle').write_bytes(pickle.dumps(out,protocol=5))
 save('preparation.json',dict(markets=len(out),start=start,end=end,full_days=7,source='Original prospectively collected features + downloaded execution quotes',feature_observations=sum(len(m['new_features']) for m in out),sha256=hashlib.sha256((P/'forward_prepared.pickle').read_bytes()).hexdigest()))
if __name__=='__main__':run()
