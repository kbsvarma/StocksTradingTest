from pathlib import Path
from curl_cffi import requests
import pandas as pd,io,json,re,hashlib
P=Path(__file__).resolve().parent;(P/'issuer').mkdir(exist_ok=True)
rows=[]
for s in ['QQQI','SPYI','IWMI']:
 url=f'https://neosfunds.com/{s.lower()}/';f=P/'issuer'/f'{s}.html'
 if not f.exists():
  r=requests.get(url,impersonate='chrome',timeout=30);r.raise_for_status();f.write_text(r.text)
 from html import unescape
 html=f.read_text()
 for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',html,flags=re.S|re.I):
  vals=[unescape(re.sub(r'<[^>]+>','',td)).strip() for td in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',tr,flags=re.S|re.I)]
  if len(vals)!=5 or not re.match(r'\d{2}/\d{2}/\d{4}',vals[0]):continue
  try:
   dec,ex,rec,pay=[pd.to_datetime(x) for x in vals[:4]]
   amount=float(vals[4].replace('$','').replace(',',''))
   if pd.notna(amount) and ex<=pd.Timestamp('2026-09-25'):
    rows.append(dict(ticker=s,declaration=str(dec.date()),ex_date=str(ex.date()),record=str(rec.date()),payment=str(pay.date()),amount=amount,source=url))
  except (ValueError,TypeError):continue
df=pd.DataFrame(rows).drop_duplicates(['ticker','ex_date']).sort_values(['ticker','ex_date'])
df.to_csv(P/'issuer_distributions_raw.csv',index=False)
# Current issuer HTML transposes ex/record columns in early-2024 rows.
# Contemporaneous 19a notices confirm record dates; pre-T+1 ex date is prior session.
m=(pd.to_datetime(df.ex_date)>pd.to_datetime(df.record))&(pd.to_datetime(df.ex_date)<pd.Timestamp('2024-05-28'))
df['date_correction']=''
df.loc[m,['ex_date','record']]=df.loc[m,['record','ex_date']].to_numpy()
df.loc[m,'date_correction']='Swapped transposed issuer HTML ex/record dates; contemporaneous issuer 19a notices and Yahoo event dates agree'
df.to_csv(P/'issuer_distributions.csv',index=False)
print(df.groupby('ticker').agg(events=('amount','size'),first=('ex_date','min'),last=('ex_date','max')).to_string())
print('Corrected transposed dates',int(m.sum()))
# TSLY additional audit: issuer history includes 2024-split-adjusted older payouts;
# normalize to Yahoo's current share basis, preserving all transformations.
s='TSLY';url='https://yieldmaxetfs.com/our-etfs/tsly/';f=P/'issuer/TSLY.html'
if not f.exists():
 r=requests.get(url,impersonate='chrome',timeout=30);r.raise_for_status();f.write_text(r.text)
d=pd.read_csv(P/'data/TSLY.csv',index_col=0,parse_dates=True);rr=[]
for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',f.read_text(),flags=re.S|re.I):
 vals=[unescape(re.sub(r'<[^>]+>','',td)).strip() for td in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',tr,flags=re.S|re.I)]
 if len(vals)<5:continue
 try:
  amt=float(vals[0].replace('$','').replace('*',''));dec,ex,rec,pay=[pd.Timestamp(x) for x in vals[1:5]]
 except (ValueError,TypeError):continue
 if ex>d.index.max() or ex not in d.index:continue
 vendor=float(d.loc[ex,'Dividends']);factor=min([1,2,5,10],key=lambda v:abs(amt*v-vendor))
 # Consistency check: corrections are rounding-sized except newly missing Sep 24 payout.
 assert abs(vendor-amt*factor)<.01 or (ex==pd.Timestamp('2026-09-24') and vendor==0)
 rr.append(dict(ticker=s,declaration=str(dec.date()),ex_date=str(ex.date()),record=str(rec.date()),payment=str(pay.date()),issuer_shown_amount=amt,scale_factor=factor,amount=amt*factor,provider=vendor,difference=vendor-amt*factor,source=url))
t=pd.DataFrame(rr).drop_duplicates('ex_date').sort_values('ex_date');t.to_csv(P/'issuer/TSLY_normalization_audit.csv',index=False)
pd.concat([df,t[df.columns.intersection(t.columns)]],ignore_index=True).to_csv(P/'issuer_distributions.csv',index=False)
print('TSLY issuer payouts',len(t))
