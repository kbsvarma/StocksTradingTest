"""Research diagnostics; public archived data only, no trading interfaces."""
from late import *
from portfolio import day, metrics
import numpy as np

key='full/original_favorite/very_late/p90/base'
rows=read(P/'extended_ledgers.json.gz')[key]
monthly=defaultdict(list)
for r in rows:monthly[day(r['entered'])[:7]].append(r)
n=len(rows);wins=sum(r['won'] for r in rows);z=1.959963984540054;p=wins/n
center=(p+z*z/(2*n))/(1+z*z/n)
radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
byday=defaultdict(float)
for r in rows:byday[day(r['entered'])]+=r['net']
start=datetime.fromisoformat(min(byday));end=datetime.fromisoformat('2026-10-09')
days=[(start+__import__('datetime').timedelta(days=i)).date().isoformat() for i in range((end-start).days+1)]
values=np.array([byday.get(d,0) for d in days]);rng=np.random.default_rng(10102026)
# Circular three-day blocks preserve short-range calendar clustering. This is
# descriptive uncertainty conditional on the selected rule, not a holdout test.
starts=rng.integers(0,len(values),size=(20000,math.ceil(len(values)/3)))
ix=(starts[:,:,None]+np.arange(3))%len(values)
totals=values[ix].reshape(20000,-1)[:,:len(values)].sum(axis=1)
out={'selected_rule':key,'monthly':{m:metrics(rr) for m,rr in sorted(monthly.items())},
 'win_rate':p,'wilson_95_interval':[center-radius,center+radius],
 'calendar_days':len(days),'active_days':len(byday),'first_entry':min(byday),'last_entry':max(byday),
 'three_day_block_95_net_interval':np.quantile(totals,[.025,.975]).tolist(),
 'bootstrap_positive_fraction':float((totals>0).mean()),
 'uncertainty_caveat':'Retrospectively selected rule and incomplete pre-July29 play history; intervals do not correct selection or establish future returns.',
 'losses':[r for r in rows if r['won'] is False]}

if (P/'data/KXMLBGAME').exists():
 coverage=defaultdict(Counter);first=[];last=[];odd=[]
 for root in ['earlier','data']:
  d=P/root/'KXMLBGAME';groups=defaultdict(list)
  for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
  for et,ms in groups.items():
   if not (d/(et+'_plays.json.gz')).exists():continue
   mile=read(d/(et+'_milestone.json'));cm=read(d/(et+'_candles.json.gz'))
   states,stamps,e,cov,h,a,tm,cs,prefav,comp=prepare_event(ms,cm,mile,'KXMLBGAME',read(d/(et+'_plays.json.gz')),None)
   month=mile['start_date'][:7];c=coverage[month];c['games']+=1;c['usable_play_games']+=bool(states);c['pregame_favorite_games']+=prefav is not None
   c['pregame_favorite_and_play_games']+=bool(states) and prefav is not None
   if states:first.append(min(stamps));last.append(max(stamps))
   if states and any(m['close_time'] and ts(m['close_time'])<e-300 for m in ms):odd.append(et)
 out['play_coverage_by_month']={m:dict(c) for m,c in sorted(coverage.items())}
 out['first_usable_play_utc']=datetime.fromtimestamp(min(first),ZoneInfo('UTC')).isoformat()
 out['market_close_before_last_play_events']=odd
 out['selected_entries_in_close_discrepancy_events']=[r['event'] for r in rows if r['event'] in odd]
 # Keep old/new quote receipts without changing archive after seeing results.
 rechecks=[]
 for r in read(P/'quote_recheck_receipt.json'):
  d=P/'data/KXMLBGAME';old=read(d/(r['event']+'_candles.json.gz'))[r['ticker']]
  raw=read(P/'quote_rechecks'/(r['ticker']+'.json.gz'))
  new=next(a['candlesticks'] for a in raw['markets'] if a['market_ticker']==r['ticker'])
  oldmap={c['end_period_ts']:quote(c) for c in old};newmap={c['end_period_ts']:quote(c) for c in new}
  close=ts(r['close_time']);changes=[t for t in set(oldmap)|set(newmap) if t<=close and oldmap.get(t)!=newmap.get(t)]
  rechecks.append({'ticker':r['ticker'],'close':close,'quote_changes_before_close':changes})
 out['quote_recheck_before_close']=rechecks
 # A settled-only zero drawdown can conceal temporary losses. Mark both fixed
 # price-band portfolios to the latest recorded bid once per minute.
 led=read(P/'extended_ledgers.json.gz');bands={}
 for band in ['p90','p95']:
  rr=led['full/original_favorite/very_late/'+band+'/base'];books={}
  for r in rr:
   fn=r['event']+'_candles.json.gz'
   d=next(d for d in [P/'data/KXMLBGAME',P/'earlier/KXMLBGAME'] if (d/fn).exists())
   cs=read(d/fn)[r['ticker']];qs=sorted((c['end_period_ts'],quote(c,r['side'])[0]) for c in cs if quote(c,r['side']))
   books[r['event']]=([q[0] for q in qs],[q[1] for q in qs])
  times=sorted(set(range(min(r['entered'] for r in rr)//60*60,max(r['settled'] for r in rr)+60,60))|{r['entered'] for r in rr}|{r['settled'] for r in rr})
  peak=100.;dd=0.;missing=0;maxage=0
  for t in times:
   done=[r for r in rr if r['settled']<=t];active=[r for r in rr if r['entered']<=t<r['settled']]
   equity=100+sum(r['net'] for r in done)-sum(r['reserve'] for r in active);valid=True
   for r in active:
    stamps,bids=books[r['event']];j=bisect.bisect_right(stamps,t)-1
    if j<0:missing+=1;valid=False;continue
    age=t-stamps[j];maxage=max(maxage,age)
    if age>120:missing+=1;valid=False
    equity+=5*bids[j]
   if valid:peak=max(peak,equity);dd=max(dd,peak-equity)
  n2=len(rr);w2=sum(r['won'] for r in rr);p2=w2/n2;cen=(p2+z*z/(2*n2))/(1+z*z/n2);rad=z*math.sqrt(p2*(1-p2)/n2+z*z/(4*n2*n2))/(1+z*z/n2)
  months=defaultdict(list)
  for r in rr:months[day(r['entered'])[:7]].append(r)
  bands[band]={'bid_marked_drawdown':round(dd,5),'missing_or_stale_held_marks':missing,'max_quote_age_seconds':maxage,'wilson_95_interval':[cen-rad,cen+rad],'monthly':{m:metrics(v) for m,v in sorted(months.items())},'mean_win':sum(r['net'] for r in rr if r['won'])/w2,'mean_loss':sum(r['net'] for r in rr if not r['won'])/(n2-w2) if n2>w2 else None}
 out['price_bands']=bands
 left={r['event'] for r in led['full/original_favorite/very_late/p90/base']};right={r['event'] for r in led['full/original_favorite/very_late/p95/base']}
 out['price_band_overlap_games']=len(left&right)
 out['mark_limit']='Minute bid marks omit gaps and do not verify executable depth or exit fees. Portfolios for the two price bands are independent and overlap; their profits cannot be added.'
save(P/'diagnostics.json',out)
print(json.dumps({k:v for k,v in out.items() if k not in ['losses','monthly','market_close_before_last_play_events','price_bands']},indent=2))
print('MONTHLY',[(m,r['trades'],r['wins'],r['losses'],r['net']) for m,r in out['monthly'].items()])
if 'price_bands' in out:print('BANDS',{k:{a:b for a,b in v.items() if a!='monthly'} for k,v in out['price_bands'].items()})
