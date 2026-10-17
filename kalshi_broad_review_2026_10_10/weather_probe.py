"""One bounded read-only check of current weather signals and five-contract books."""
from weather_model import *
import time
out=[]
for city in CITIES:
 dest=P/'replays'/city;bars={}
 for path in sorted(dest.glob('*_source.json.gz')):
  for p in read(path)['live_data']['details']['timeseries']:
   if p['v'].get('temp_f') is not None:bars[int(p['t'])//1000]=(float(p['v']['temp_f']),p['v']['status'])
 old=read(dest/'markets.json');byevent={m['event_ticker']:m for m in old};errors={}
 for model in ('persistence','trend'):
  for h in (30,15,5):
   es=[]
   for m in byevent.values():
    cl=ts(m['close_time'])
    if not START<=cl<SPLIT or ts(m['settlement_ts'])>=SPLIT:continue
    f=forecast(bars,cl-h*60,cl,model,10)
    if f is not None:es.append(float(m['expiration_value'])-f)
   errors[(model,h)]=es
 live=get('/markets',{'series_ticker':city,'status':'open','limit':1000},False)['markets'];now=time.time();live=[m for m in live if ts(m['open_time'])<=now<ts(m['close_time'])]
 if not live:out.append({'city':city,'no_current':True});continue
 close=min(ts(m['close_time']) for m in live);live=[m for m in live if ts(m['close_time'])==close];event=live[0]['event_ticker'];chart=get('/live_data/events/'+event,{'range':'1h'},False);save(P/'weather_live_probe'/f'{city}_source.json',chart)
 for p in chart['live_data']['details']['timeseries']:
  if p['v'].get('temp_f') is not None:bars[int(p['t'])//1000]=(float(p['v']['temp_f']),p['v']['status'])
 decision=int(now)//60*60;h=(close-decision)//60;row={'city':city,'event':event,'decision':decision,'captured_ts':time.time(),'minutes_remaining':h,'latest_source_ts':max(bars),'source_age_seconds':time.time()-max(bars),'models':[]}
 if h not in (30,15,5):row['reason']='Outside predetermined decision horizons';out.append(row);continue
 candles=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in live),'start_ts':decision-660,'end_ts':decision,'period_interval':1},False);save(P/'weather_live_probe'/f'{city}_candles.json',candles)
 cmap={r['market_ticker']:{c['end_period_ts']:c for c in r['candlesticks']} for r in candles['markets']}
 for model in ('persistence','trend'):
  pred=forecast(bars,decision,close,model,10);errs=errors[(model,h)];choices=[]
  if pred is None or len(errs)<60:row['models'].append({'model':model,'reason':'Source/calibration coverage missing'});continue
  for m in live:
   cs=cmap.get(m['ticker'],{});pair=quote(cs.get(decision))
   if pair is None or sum(float(c.get('volume_fp') or 0) for t,c in cs.items() if decision-600<t<=decision)<5:continue
   py=(sum(pred+e>float(m['floor_strike']) for e in errs)+1)/(len(errs)+2)
   for side,p in [('yes',py),('no',1-py)]:
    bid,ask=sq(pair,side);edge=p-ask-fee(ask,1)/5-.01
    if .60<=ask<.95 and ask-bid<=.02000001 and edge>=.02:choices.append({'model':model,'ticker':m['ticker'],'side':side,'ask':ask,'p':p,'edge':edge,'cap':min(.95,ask+.01),'forecast':pred})
  if not choices:row['models'].append({'model':model,'forecast':pred,'reason':'No eligible quote/model edge'});continue
  best=max(choices,key=lambda x:(x['edge'],-x['ask'],x['ticker']));book=get('/markets/'+best['ticker']+'/orderbook',{'depth':100},False);save(P/'weather_live_probe'/f'{city}_{model}_book.json',book)
  levels=sorted((1-float(p),float(q)) for p,q in book['orderbook_fp']['no_dollars' if best['side']=='yes' else 'yes_dollars']);best['size_at_cap']=sum(q for p,q in levels if p<=best['cap']+1e-8);best['book_captured_ts']=time.time();best['book_age_from_decision']=time.time()-decision;row['models'].append(best)
 out.append(row)
save(P/'weather_live_probe.json',out);print(out)
