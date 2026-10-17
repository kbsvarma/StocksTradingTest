from pathlib import Path
import yfinance as yf
import pandas as pd
import json, hashlib, datetime

P=Path(__file__).resolve().parent
(P/'data').mkdir(exist_ok=True)
manifest=[]
for s in ['AAPL','AMZN','GOOGL','GOOG','META','MSFT','NVDA','TSLA','QQQ','SPY']:
    f=P/'data'/f'{s}.csv'
    d=yf.Ticker(s).history(start='2012-01-01',end='2026-09-25',auto_adjust=False,actions=True,raise_errors=True)
    d.index=pd.to_datetime(d.index.date)
    assert len(d)>1000 and not d.index.duplicated().any()
    assert d.index.max()<=pd.Timestamp('2026-09-24')
    d.to_csv(f,index_label='Date')
    row=dict(ticker=s,rows=len(d),start=str(d.index.min().date()),end=str(d.index.max().date()),sha256=hashlib.sha256(f.read_bytes()).hexdigest(),retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source='Yahoo Finance via yfinance',yfinance_version=yf.__version__)
    old=Path('stock_overnight_2026_09/data')/f'{s}.parquet'
    if old.exists():
        a=pd.read_parquet(old);a.index=pd.to_datetime(a.index.date); ix=a.index.intersection(d.index)
        row['old_cache_overlap']=len(ix)
        row['max_close_relative_difference']=float((a.loc[ix,'Close']/d.loc[ix,'Close']-1).abs().max())
        row['max_daily_total_return_difference']=float((a.loc[ix,'Adj Close'].pct_change()-d.loc[ix,'Adj Close'].pct_change()).abs().max())
    manifest.append(row)
    print(row,flush=True)
(P/'data_manifest.json').write_text(json.dumps(manifest,indent=2))
