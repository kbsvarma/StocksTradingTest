"""Twelve fixed historical examples: times, favorite side and official target."""
import datetime,json,math,time
from pathlib import Path
import requests
P=Path(__file__).resolve().parent;D=P/'contract_receipts';D.mkdir(exist_ok=True)
rows=json.loads((P/'captured.json').read_text());S=requests.Session()
BASE='https://api.elections.kalshi.com/trade-api/v2'
def fetch(name,path,params=None):
 p=D/(name+'.json')
 if p.exists():return json.loads(p.read_text())
 time.sleep(2.5)
 if time.time()%60<7:time.sleep(7-time.time()%60)
 for i in range(3):
  start=time.time();r=S.get(BASE+path,params=params,timeout=5)
  if r.status_code!=429:break
  time.sleep(5+i*3)
 r.raise_for_status();out=dict(url=r.url,requested=start,received=time.time(),data=r.json());p.write_text(json.dumps(out)+'\n');return out
out=[]
for day in (10,12,15):
 for asset in ('BTC','ETH','SOL','XRP'):
  r=max((r for r in rows if r['day']==day and r['asset']==asset and r['lane']=='cushion_z2' and r.get('z') is not None),key=lambda x:x['z'])
  ticker=r['ticker'];T=r['decision_ts'];m=fetch(ticker,'/markets/'+ticker)['data']['market']
  candles=fetch(ticker+'_candles','/series/'+ticker.split('-')[0]+'/markets/'+ticker+'/candlesticks',dict(start_ts=T-60,end_ts=T,period_interval=1))['data']['candlesticks']
  c=next(x for x in candles if x['end_period_ts']==T)
  a=float(c['yes_ask']['close_dollars']);b=float(c['yes_bid']['close_dollars']);side='yes' if a+b>1 else 'no'
  close=datetime.datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp();opened=datetime.datetime.fromisoformat(m['open_time'].replace('Z','+00:00')).timestamp()
  strike=m.get('floor_strike');strike=float(strike) if strike is not None else None
  z_official=((1 if r['side']=='yes' else -1)*(r['spot']/strike-1)/(r['sigma']*math.sqrt(r['remaining_minutes']))) if strike else None
  out.append(dict(day=day,asset=asset,ticker=ticker,T=T,open_matches=opened==T+r['remaining_minutes']*60-900,close_matches=close==T+r['remaining_minutes']*60,
    favorite_matches=side==r['side'],captured_side=r['side'],captured_ask=r['signal_ask'],refetched_ask=a if side=='yes' else 1-b,
    reference_proxy=r['strike_proxy'],official_strike=strike,reference_difference_bp=(r['strike_proxy']/strike-1)*10000 if strike else None,
    captured_z=r['z'],official_strike_z=z_official,rule_primary=m.get('rules_primary'),strike_type=m.get('strike_type')))
(P/'contract_checks.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps([{k:v for k,v in x.items() if k!='rule_primary'} for x in out],indent=2))
