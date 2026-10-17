"""Read-only API/schema canary, stored outside the prospective trading ledger."""
import json,sqlite3,time
from pathlib import Path
from observer import Client,P
from engine import book_fill,quote
def run():
 db=sqlite3.connect('file:'+str(P/'paper.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;c=Client();receipts=[];out=[]
 def get(path,params=None):
  r=c.get(path,params);receipts.append(r)
  if r['error']:raise RuntimeError(r['error'])
  return r['body']
 for series in ('KXATPMATCH','KXWTAMATCH'):
  row=db.execute('SELECT * FROM events WHERE series=? ORDER BY start LIMIT 1',(series,)).fetchone();info=json.loads(row['info']);mi=info['milestone'];m=info['markets'][0];ticker=m['ticker']
  meta=get('/series/'+series)['series'];ob=get('/markets/'+ticker+'/orderbook',{'depth':0});fill,reason=book_fill(ob,'yes',.99);data=get('/live_data/milestone/'+mi['id']+'/game_stats')
  boundary=int(time.time()//60)*60-60
  cs=get('/markets/candlesticks',dict(market_tickers=ticker,start_ts=boundary-120,end_ts=boundary,period_interval=1))
  candles=[x for group in cs.get('markets',[]) for x in group.get('candlesticks',[])]
  out.append(dict(series=series,event=row['event'],fee_type=meta.get('fee_type'),fee_multiplier=meta.get('fee_multiplier'),milestone_competitors=[mi['details'].get('first_competitor_id'),mi['details'].get('second_competitor_id')],market_competitors=[x.get('custom_strike',{}).get('tennis_competitor') for x in info['markets']],book_fill_example=fill,book_reason=reason,score_keys=list(data),candle_count=len(candles),latest_quote=quote(candles[-1]) if candles else None))
 result=dict(at=time.time(),summary=out,receipts=receipts);(P/'canary_receipts.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(out,indent=2));db.close()
if __name__=='__main__':run()
