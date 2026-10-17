from pathlib import Path
import pandas as pd, yfinance as yf, json,hashlib,datetime,time
P=Path(__file__).resolve().parent;(P/'data').mkdir(exist_ok=True)
manifest=[]
for s in 'O VZ PM MO XOM CVX PEP KO JNJ PG SO DUK'.split():
    f=P/'data'/f'{s}.csv'
    if f.exists(): d=pd.read_csv(f,index_col=0,parse_dates=True)
    else:
        for attempt in range(3):
            try:
                d=yf.Ticker(s).history(start='2014-01-01',end='2026-09-26',auto_adjust=False,actions=True,raise_errors=True)
                d.index=pd.to_datetime(d.index.date)
                assert len(d)>2500
                d.to_csv(f,index_label='Date');break
            except Exception:
                if attempt==2:raise
                time.sleep(2)
    assert not d.index.duplicated().any() and d.index.max()<=pd.Timestamp('2026-09-25')
    row=dict(ticker=s,rows=len(d),first=str(d.index.min().date()),last=str(d.index.max().date()),dividends=int((d.Dividends>0).sum()),splits=d.loc[d['Stock Splits']!=0,'Stock Splits'].to_dict(),sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    row['splits']={str(k.date()):v for k,v in row['splits'].items()};manifest.append(row)
    print(row,flush=True)
(P/'manifest.json').write_text(json.dumps(dict(retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source='Yahoo Finance via yfinance',version=yf.__version__,files=manifest),indent=2))
