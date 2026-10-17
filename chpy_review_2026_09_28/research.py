from pathlib import Path
import sys, re, json, hashlib, inspect
from html import unescape
import pandas as pd
import numpy as np
import yfinance as yf
from curl_cffi import requests

P = Path(__file__).resolve().parent
BASE = P.parent / 'dividend_capture_broad_2026_09_28'
for s in ['CHPY', 'SMH', 'SOXX']:
    f = P / 'data' / f'{s}.csv'
    if not f.exists():
        d = yf.Ticker(s).history(start='2025-04-01', end='2026-09-26', auto_adjust=False, actions=True, raise_errors=True)
        d.index = pd.to_datetime(d.index.date)
        d.to_csv(f, index_label='Date')
    print('Downloaded', s, flush=True)

url = 'https://yieldmaxetfs.com/our-etfs/chpy/'
f = P / 'issuer/CHPY.html'
if not f.exists():
    r = requests.get(url, impersonate='chrome', timeout=30)
    r.raise_for_status()
    f.write_text(r.text)
rows = []
for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', f.read_text(), re.S | re.I):
    vals = [unescape(re.sub(r'<[^>]+>', '', td)).strip() for td in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
    if len(vals) < 5 or not re.match(r'^\$[0-9]', vals[0]):
        continue
    try:
        amt = float(vals[0].replace('$', '').replace('*', ''))
        dec, ex, rec, pay = [pd.Timestamp(x) for x in vals[1:5]]
    except (ValueError, TypeError):
        continue
    if ex > pd.Timestamp('2026-09-25'):
        continue
    rows.append(dict(ticker='CHPY', declaration=str(dec.date()), ex_date=str(ex.date()), record=str(rec.date()), payment=str(pay.date()), amount=amt, estimated_ROC=vals[5] if len(vals)>5 else '', source=url))
iss = pd.DataFrame(rows).drop_duplicates('ex_date').sort_values('ex_date')
iss.to_csv(P / 'issuer_distributions.csv', index=False)

# Reuse the saved research engine without modifying any of its files or results.
sys.path.insert(0, str(BASE))
import portfolio as engine
engine.P = P
DATES, N, END, DAY = engine.DATES, engine.N, engine.END, engine.DAY
audits = []
for s in ['CHPY', 'SMH', 'SOXX']:
    d = pd.read_csv(P / 'data' / f'{s}.csv', index_col=0, parse_dates=True)
    assert not d.index.duplicated().any() and d.index.isin(DATES).all()
    assert (d['Stock Splits'] == 0).all(), 'Split adjustment needs review'
    cash = d.Dividends.copy() + d.get('Capital Gains', 0)
    pays, decs = {}, {}
    if s == 'CHPY':
        assert set(d.index[cash>0]).issubset(set(pd.to_datetime(iss.ex_date))), 'Missing issuer payout'
        for row in iss.itertuples():
            ex = pd.Timestamp(row.ex_date)
            k = DATES.get_loc(ex)
            assert ex in d.index
            audits.append(dict(ex_date=row.ex_date, vendor=float(cash.loc[ex]), issuer=row.amount))
            cash.loc[ex] = row.amount
            pays[k] = int(DATES.searchsorted(pd.Timestamp(row.payment)))
            decs[k] = int(DATES.searchsorted(pd.Timestamp(row.declaration)))
    start = DATES.get_loc(d.index[0])
    a = d.reindex(DATES)
    c = a.Close.to_numpy()
    v = cash.reindex(DATES).fillna(0).to_numpy()
    z = pd.Series(v, index=DATES)
    due = np.searchsorted(DAY, DAY+5)
    for k, pay in pays.items():
        due[k] = pay
    total = (c+v)/np.roll(c, 1)-1
    total[:start+1] = 0
    assert np.isfinite(c[start:]).all() and (c[start:]>0).all()
    engine.DATA[s] = dict(close=c, div=v, due=due, decl=decs, start=start, total=total, raw=d,
                          ttm=z.rolling('365D', closed='left').sum().to_numpy()/c,
                          priority=np.zeros(N), adv=(a.Close*a.Volume).shift(1).rolling(20,min_periods=5).mean().to_numpy())
    for b in engine.BEFORE:
        for after in engine.AFTER:
            # Include observed ex-dates even when planned exit is beyond cutoff.
            engine.GROUP[(s,b,after)] = [dict(i=int(k-b), k=int(k), j=int(k+after)) for k in np.flatnonzero(v>0) if k-b>=start]
pd.DataFrame(audits).to_csv(P / 'issuer_audit.csv', index=False)

# Mark open positions, preserving cash-flow reconciliation; never fabricate a sale.
source = inspect.getsource(engine.simulate)
source = source.replace('def simulate(', 'def simulate_asof(')
source = source.replace('l=pd.DataFrame(ledger,columns=', "closed_count=len(ledger)\n for s,p in pos.items():\n  if not buyhold:\n   value=p['q']*DATA[s]['close'][-1]\n   ledger.append((s,p['entry'],END,p['spent'],value,p['div'],value+p['div']-p['spent']))\n l=pd.DataFrame(ledger,columns=")
source = source.replace('assert not pos', 'assert len(pos)<=slots')
source = source.replace('trades=len(l)', 'trades=closed_count')
source = source.replace('if keep:\n', "row['open_at_cutoff']=len(pos)\n if keep:\n")
exec(source, engine.__dict__)
simulate = engine.simulate_asof
start_date = str(DATES[engine.DATA['CHPY']['start']].date())
results = []
for b in engine.BEFORE:
    for a in engine.AFTER:
        results.append(dict(ticker='CHPY', **simulate(['CHPY'], b, a, start_date=start_date, keep=f'CHPY_b{b}_a{a}')))
for s in ['CHPY', 'SMH', 'SOXX', 'TSLY', 'SMCY']:
    if s not in engine.DATA:
        continue
    results.append(dict(ticker=s, **simulate([s], start_date=start_date, buyhold=True, keep=f'{s}_buyhold')))
pd.DataFrame(results).to_csv(P / 'results.csv', index=False)
sens = []
for cost in [0, .001, .002]:
    for b,a in [(1,1),(5,5)]:
        sens.append(simulate(['CHPY'],b,a,start_date=start_date,cost=cost))
pd.DataFrame(sens).to_csv(P / 'cost_sensitivity.csv', index=False)
periods=[]
for dt in ['2025-09-25','2026-01-02','2026-06-25']:
    for s in ['CHPY','SMH','SOXX']:
        periods.append(dict(ticker=s,period_start=dt,**simulate([s],start_date=dt,buyhold=True)))
    for b,a in [(1,1),(5,5)]:
        periods.append(dict(ticker='CHPY',period_start=dt,**simulate(['CHPY'],b,a,start_date=dt)))
pd.DataFrame(periods).to_csv(P/'periods.csv',index=False)

d = engine.DATA['CHPY']
start = d['start']
q = 100000/(d['close'][start]*1.0005)
cash_received = float(sum(q*d['div'][k] for k in range(start+1,N) if d['due'][k]<=END))
receivable = float(sum(q*d['div'][k] for k in range(start+1,N) if d['due'][k]>END))
shares_value = q*d['close'][-1]
equity = q*d['close'][start:] + q*np.cumsum(d['div'][start:])
peak = np.maximum.accumulate(np.r_[100000,equity])[1:]
details = dict(start=start_date,end=str(DATES[-1].date()),payout_count=len(iss),
    first_close=d['close'][start],last_close=d['close'][-1],price_return=d['close'][-1]/d['close'][start]-1,
    total_distributions_per_share=float(d['div'].sum()),shares_value_without_reinvestment=shares_value,
    cash_received_without_reinvestment=cash_received,receivable_without_reinvestment=receivable,
    total_wealth_without_reinvestment=shares_value+cash_received+receivable,max_drawdown_without_reinvestment=float((equity/peak-1).min()),
    last_payout=float(iss.amount.iloc[-1]),last_payout_on_100k_at_last_close=100000/d['close'][-1]*float(iss.amount.iloc[-1]),
    trailing_52week_distributions=float(iss.loc[pd.to_datetime(iss.ex_date)>DATES[-1]-pd.Timedelta(days=364),'amount'].sum()))
(P/'details.json').write_text(json.dumps(details,indent=2))
(P/'manifest.json').write_text(json.dumps(dict(price_source='Yahoo Finance via yfinance',issuer=url,
    files={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in list((P/'data').glob('*.csv'))+[P/'issuer/CHPY.html']}),indent=2))
print(json.dumps(details,indent=2))
r = pd.DataFrame(results)
print(r[['ticker','before','after','buyhold','ending_value','total_return','max_drawdown','exposure','trades','open_at_cutoff']].to_string(index=False))
