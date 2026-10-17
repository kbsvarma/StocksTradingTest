import json,time,concurrent.futures
from pathlib import Path
import pandas as pd
import yfinance as yf
P=Path(__file__).resolve().parent
(P/'profiles').mkdir(exist_ok=True)
symbols=json.loads((P/'EXPANSION_COUNTS.json').read_text())['new_eligible_symbols']
keys=['symbol','longName','sector','industry','marketCap','totalRevenue','profitMargins','operatingMargins','returnOnEquity','totalDebt','totalCash','ebitda','freeCashflow','operatingCashflow','currentRatio','trailingPE','forwardPE','revenueGrowth','earningsGrowth','fullTimeEmployees','financialCurrency','currency','mostRecentQuarter','lastFiscalYearEnd','website','quoteType']
def fetch(sym):
 f=P/'profiles'/f'{sym}.json'
 if f.exists():return json.loads(f.read_text())
 try:
  d=yf.Ticker(sym.replace('.','-')).get_info();r={k:d.get(k) for k in keys};r['ticker']=sym;r['retrieved_at']=pd.Timestamp.now(tz='UTC').isoformat();r['status']='ok' if d.get('sector') else 'missing_sector'
 except Exception as e:r={'ticker':sym,'status':'error','error':str(e)[:200]}
 f.write_text(json.dumps(r,indent=2));time.sleep(.12);return r
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 for k,r in enumerate(ex.map(fetch,symbols),1):
  rows.append(r)
  if k%50==0:print(k,len(symbols),pd.Series([r['status'] for r in rows]).value_counts().to_dict(),flush=True)
pd.concat([pd.read_csv(P.parent/'cover_risk_review_2026_09/company_profiles.csv'),pd.DataFrame(rows)],ignore_index=True).drop_duplicates('ticker',keep='last').to_csv(P/'company_profiles.csv',index=False)
print('DONE',len(rows),flush=True)
