from pathlib import Path
import json,time,pandas as pd,yfinance as yf
P=Path(__file__).resolve().parent;initial=pd.read_csv(P/'company_profiles.csv');initial.to_csv(P/'company_profiles_first_pass.csv',index=False)
keys=['symbol','longName','sector','industry','marketCap','totalRevenue','profitMargins','operatingMargins','returnOnEquity','totalDebt','totalCash','ebitda','freeCashflow','operatingCashflow','currentRatio','trailingPE','forwardPE','revenueGrowth','earningsGrowth','fullTimeEmployees','financialCurrency','currency','mostRecentQuarter','lastFiscalYearEnd','website','quoteType']
pending=[]
for sym in initial.ticker:
 r=json.loads((P/'profiles'/f'{sym}.json').read_text())
 if r['status']=='error':pending.append(sym)
for k,sym in enumerate(pending,1):
 try:
  d=yf.Ticker(sym.replace('.','-')).get_info();r={key:d.get(key) for key in keys};r.update(ticker=sym,retrieved_at=pd.Timestamp.now(tz='UTC').isoformat(),status='ok' if d.get('sector') else 'missing_sector');(P/'profiles'/f'{sym}.json').write_text(json.dumps(r,indent=2))
 except Exception as e:
  print(sym,str(e),flush=True)
  if 'Rate limited' in str(e):break
 if k%25==0:print(k,len(pending),flush=True)
 time.sleep(.4)
rows=[json.loads((P/'profiles'/f'{sym}.json').read_text()) for sym in initial.ticker];pd.DataFrame(rows).to_csv(P/'company_profiles.csv',index=False);print(pd.DataFrame(rows).status.value_counts().to_dict(),flush=True)
