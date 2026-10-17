"""Resolve a company name or ticker before launching a security investigation."""
import json
import re
from pathlib import Path
from difflib import SequenceMatcher
from .collectors import symbol

NAMES={'NVIDIA':'NVDA','NIVIDIA':'NVDA','NVDIA':'NVDA','MICROSOFT':'MSFT','APPLE':'AAPL','AMAZON':'AMZN','ALPHABET':'GOOGL','GOOGLE':'GOOGL','TESLA':'TSLA','META':'META','PALANTIR':'PLTR'}
LABELS={'NVDA':'NVIDIA','MSFT':'Microsoft','AAPL':'Apple','AMZN':'Amazon','GOOGL':'Alphabet','TSLA':'Tesla','META':'Meta Platforms','PLTR':'Palantir'}
class AmbiguousCompany(ValueError):
    def __init__(self,choices):
        self.choices=choices
        super().__init__('Several securities match. Choose the company and listing below.')

def resolve(query,data=None,search=None):
    q=' '.join(str(query).strip().upper().split())
    if not q or len(q)>100:raise ValueError('Enter a company name or stock ticker.')
    if q in NAMES:return NAMES[q]
    if q in LABELS:return q
    # Recognizable name typos, not approximate short ticker substitution.
    if len(q)>=5:
        close=sorted(((SequenceMatcher(None,q,n).ratio(),v) for n,v in NAMES.items()),reverse=True)
        if close[0][0]>=.83 and (close[0][0]-next((s for s,v in close if v!=close[0][1]),0))>=.12:return close[0][1]
    if data:
        try:
            mapping=json.loads((Path(data)/'intelligence/investigations/cik_map.json').read_text())
            if q in mapping:return symbol(q)
        except (OSError,ValueError):pass
    if search is None:
        import yfinance as yf
        search=lambda text:yf.Search(text,max_results=8,news_count=0,enable_fuzzy_query=True,timeout=8).quotes
    try:quotes=search(q)
    except Exception as exc:raise ValueError('Company search is temporarily unavailable. Retry shortly; no investigation was started.') from exc
    options=[]
    for r in quotes:
        if r.get('quoteType') not in {'EQUITY','ETF'}:continue
        try:t=symbol(r.get('symbol',''))
        except ValueError:continue
        name=r.get('shortname') or r.get('longname') or t
        options.append({'ticker':t,'name':name,'exchange':r.get('exchDisp') or r.get('exchange','')})
    options=list({r['ticker']:r for r in options}.values())
    exact=[r for r in options if r['ticker']==q]
    if exact:return exact[0]['ticker']
    if len(options)==1:return options[0]['ticker']
    if options:raise AmbiguousCompany(options)
    raise ValueError(f'No stock found for “{query}”. Check the name or ticker; no company report was generated.')
