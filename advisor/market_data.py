"""On-demand public market snapshots. Failed refreshes never erase good history."""
import json
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
from advisor.investigator.collectors import symbol,clean,frame_rows
from advisor.investigator.engine import atomic

FIELDS=('shortName','longName','sector','industry','currency','exchange','marketCap','trailingPE','forwardPE','trailingEps','dividendYield','fiftyTwoWeekLow','fiftyTwoWeekHigh','regularMarketPrice','regularMarketTime','regularMarketChangePercent','regularMarketChange','regularMarketOpen','regularMarketDayLow','regularMarketDayHigh','regularMarketVolume','postMarketPrice','postMarketTime','postMarketChangePercent','heldPercentInstitutions','heldPercentInsiders','shortPercentOfFloat','dateShortInterest','website')

def load(data,ticker):
    try:return json.loads((Path(data)/'market'/f'{symbol(ticker)}.json').read_text())
    except (OSError,ValueError):return {}

def refresh(data,ticker,*,factory=None):
    ticker=symbol(ticker)
    if factory is None:
        import yfinance as yf
        factory=yf.Ticker
    old=load(data,ticker);tk=factory(ticker);errors=[];out=dict(old)
    now=datetime.now(timezone.utc).isoformat()
    try:
        history=tk.history(period='5y',auto_adjust=True,actions=False,timeout=15)
        if history is None or history.empty:raise ValueError('No price history returned')
        out['bars']=frame_rows(history);out['history_as_of']=history.index[-1].isoformat();out['history_retrieved_at']=now
    except Exception as exc:errors.append('Price history: '+type(exc).__name__)
    try:
        info=tk.info or {}
        if not info.get('regularMarketPrice'):raise ValueError('No reference quote returned')
        out['profile']={k:clean(info.get(k)) for k in FIELDS};out['profile_retrieved_at']=now
    except Exception as exc:errors.append('Company snapshot: '+type(exc).__name__)
    out.update(ticker=ticker,refresh_attempted_at=now,errors=errors,source=f'https://finance.yahoo.com/quote/{ticker}/',basis='Adjusted daily OHLC; delayed/reference quotes')
    atomic(Path(data)/'market'/f'{ticker}.json',out)
    return out

def frame(snapshot):
    raw=pd.DataFrame(snapshot.get('bars',[]))
    if raw.empty:return raw
    date=next((c for c in raw if c.lower() in {'date','datetime','index'}),None)
    if not date or 'Close' not in raw:return pd.DataFrame()
    raw.index=pd.to_datetime(raw[date],utc=True,errors='coerce');raw=raw[raw.index.notna()].sort_index()
    for col in ('Open','High','Low','Close','Volume'):
        if col in raw:raw[col]=pd.to_numeric(raw[col],errors='coerce')
    return raw.dropna(subset=['Close'])

def watch_symbols(store):
    saved=store.workspace('watchlist')
    return saved.get('symbols',['NVDA','AAPL','MSFT','GOOGL'])

def save_symbols(store,symbols):
    symbols=list(dict.fromkeys(symbol(s) for s in symbols))
    if len(symbols)>100:raise ValueError('Watchlists support up to 100 securities.')
    store.save_workspace({'symbols':symbols},'watchlist')
    return symbols
