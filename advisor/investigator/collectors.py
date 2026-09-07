"""Bounded live collectors. Every response remains source- and time-attributed."""
from __future__ import annotations
import json
import math
import os
import re
import socket
import ipaddress
import time
import threading
_SEC_LOCK=threading.Lock()
_SEC_LAST=0.0
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urljoin
from pathlib import Path
from .temporal import record, utcnow, iso


def symbol(value):
    value=str(value).strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}(?:[.\-][A-Z0-9]{1,3})?',value):
        raise ValueError('Use a stock ticker such as NVDA or BRK-B')
    return value


def clean(value):
    if isinstance(value,dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if hasattr(value,'item'): return clean(value.item())
    if isinstance(value,float) and not math.isfinite(value): return None
    if isinstance(value,datetime): return iso(value)
    if value is None or isinstance(value,(str,int,float,bool)): return value
    return str(value)


def public_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('Evidence requires a public HTTPS URL')
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Non-public evidence host rejected')
    return url


def fetch(url, *, params=None, json_body=None):
    import requests
    for _ in range(4):
        public_url(url)
        if (urlparse(url).hostname or '').endswith('sec.gov'):
            global _SEC_LAST
            with _SEC_LOCK:
                wait=.17-(time.monotonic()-_SEC_LAST)
                if wait>0:time.sleep(wait)
                _SEC_LAST=time.monotonic()
        headers={'User-Agent':os.environ.get('SEC_USER_AGENT','Advisor research contact varmakbs001@gmail.com')}
        with requests.request('POST' if json_body else 'GET',url,params=params,json=json_body,
                              headers=headers,timeout=(8,20),allow_redirects=False,stream=True) as r:
            if r.status_code in (301,302,303,307,308):
                url=urljoin(url,r.headers['Location']);params=None;continue
            r.raise_for_status()
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>12_000_000: raise ValueError('Evidence response exceeds 12MB')
                chunks.append(chunk)
            return b''.join(chunks)
    raise ValueError('Too many evidence redirects')


def getjson(url, **kwargs): return json.loads(fetch(url,**kwargs))


def frame_rows(frame):
    if frame is None or getattr(frame,'empty',True): return []
    return clean(frame.reset_index().to_dict(orient='records'))


def sec(ticker, data, *, get=getjson):
    from advisor.research.edgar import CONCEPTS, DEI_CONCEPTS
    started=utcnow();rows=[];errors=[]
    cache=Path(data)/'intelligence'/'investigations'/'cik_map.json'
    mapping=None
    if cache.exists() and time.time()-cache.stat().st_mtime<7*86400:
        try: mapping=json.loads(cache.read_text())
        except (ValueError,OSError): pass
    if mapping is None:
        raw=get('https://www.sec.gov/files/company_tickers.json')
        mapping={v['ticker']:v['cik_str'] for v in raw.values()}
        cache.parent.mkdir(parents=True,exist_ok=True)
        # Unique temporary prevents concurrent investigators sharing a partial map.
        import uuid
        tmp=cache.with_suffix('.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(mapping));tmp.replace(cache)
    cik=mapping.get(ticker) or mapping.get(ticker.replace('.','-'))
    if not cik:return [],['SEC identifier unavailable for this symbol']
    facts_url=f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json'
    sub_url=f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json'
    recent={};accepted={}
    try:
        sub=get(sub_url);recent=sub.get('filings',{}).get('recent',{})
        for i,acc in enumerate(recent.get('accessionNumber',[])):
            accepted[acc]=(recent.get('acceptanceDateTime') or [None]*len(recent['accessionNumber']))[i]
        identity={'cik':int(cik),'name':sub.get('name'),'sic':sub.get('sic'),'sic_description':sub.get('sicDescription')}
        rows.append(record(ticker=ticker,source='sec_filings',kind='profile',payload=identity,
            retrieved_at=utcnow(),observed_at=utcnow(),url=sub_url,authority='primary',title='Issuer identity'))
        keep={'10-Q','10-K','8-K','6-K','20-F','4','144','SC 13D','SC 13D/A','SC 13G','SC 13G/A','S-3','S-3ASR','424B5','NT 10-Q','NT 10-K'}
        essential_seen=set();filing_count=0
        for i,form in enumerate(recent.get('form',[])):
            if form not in keep:continue
            filed=recent['filingDate'][i]
            essential=form in {'10-K','10-Q','20-F'} and form not in essential_seen
            if essential:essential_seen.add(form)
            if filed<(started-timedelta(days=120)).date().isoformat() and not essential:continue
            if filing_count>=30 and not essential:continue
            acc=recent['accessionNumber'][i];doc=recent['primaryDocument'][i]
            url=f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-","")}/{doc}'
            rows.append(record(ticker=ticker,source='sec_filings',kind='filing',payload={'form':form,'accession':acc,'report_date':recent.get('reportDate',['']*len(recent['form']))[i],'items':recent.get('items',['']*len(recent['form']))[i]},
                published_at=accepted.get(acc) or filed,retrieved_at=utcnow(),url=url,authority='primary',
                title=f'{form} filed {filed}',independence=f'sec:{acc}'))
            filing_count+=1
    except Exception as exc:errors.append('SEC submissions: '+type(exc).__name__)
    try:
        raw=get(facts_url);retrieved=utcnow()
        extra={'capex':['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],'inventory':['InventoryNet'],'receivables':['AccountsReceivableNetCurrent'],
               'sbc':['ShareBasedCompensation'],'current_assets':['AssetsCurrent'],
               'current_liabilities':['LiabilitiesCurrent'],'interest_expense':['InterestExpense'],
               'cost_of_revenue':['CostOfRevenue','CostOfGoodsAndServicesSold']}
        for tax,mapping in [('us-gaap',{**CONCEPTS,**extra}),('dei',DEI_CONCEPTS)]:
            for metric,tags in mapping.items():
                candidates=[]
                for tag in tags:
                    node=raw.get('facts',{}).get(tax,{}).get(tag,{})
                    for unit,observations in node.get('units',{}).items():
                        if unit not in {'USD','USD/shares','shares'}:continue
                        for o in observations:
                            if not o.get('filed') or not o.get('end') or o.get('form') not in {'10-Q','10-K','20-F','40-F'}:continue
                            if o['end']<(started-timedelta(days=820)).date().isoformat():continue
                            duration=(datetime.fromisoformat(o['end'])-datetime.fromisoformat(o.get('start',o['end']))).days
                            dclass='instant' if not o.get('start') else 'quarter' if 70<=duration<=110 else 'annual' if 330<=duration<=380 else 'ytd'
                            pub=accepted.get(o.get('accn')) or o['filed']
                            if datetime.fromisoformat(iso(pub))>retrieved:continue
                            candidates.append((tag,unit,o,dclass,pub))
                # Latest filing for each exact measurement period/unit, not latest filing globally.
                chosen={}
                for tag,unit,o,dclass,pub in sorted(candidates,key=lambda x:(x[2]['filed'],x[2].get('accn',''),-tags.index(x[0]))):
                    key=(unit,o.get('start'),o['end'])
                    chosen[key]=(tag,unit,o,dclass,pub)
                for tag,unit,o,dclass,pub in chosen.values():
                    rows.append(record(ticker=ticker,source='sec_facts',kind='fundamental',
                        payload={'metric':metric,'value':o['val'],'unit':unit,'tag':tag,'duration_class':dclass,'form':o['form'],'accession':o.get('accn')},
                        retrieved_at=retrieved,published_at=pub,period_start=o.get('start'),period_end=o['end'],url=facts_url,
                        authority='primary',independence=f'sec:{o.get("accn")}',title=f'{metric} {o.get("start","")} to {o["end"]}'))
    except Exception as exc:errors.append('SEC facts: '+type(exc).__name__)
    # Read actual latest quarterly/annual filing plus recent material releases, not just metadata.
    eligible=[r for r in rows if r['kind']=='filing' and r['payload']['form'] in {'10-Q','10-K','8-K','6-K','20-F'}]
    selected=[]
    for form in ('10-Q','10-K','20-F'):
        latest=next((r for r in eligible if r['payload']['form']==form),None)
        if latest:selected.append(latest)
    earnings=next((r for r in eligible if r['payload']['form']=='8-K' and '2.02' in r['payload'].get('items','')),None)
    if earnings and earnings not in selected:selected.append(earnings)
    selected+=( [r for r in eligible if r not in selected][:max(0,5-len(selected))] )
    for r in selected:
        try:
            body=document(r['url']);now=utcnow()
            periodic=r['payload']['form'] in {'10-Q','10-K','20-F'}
            period=r['payload'].get('report_date') if periodic else None
            rows.append(record(ticker=ticker,source='sec_exhibits',kind='document',payload={'text':body['text'],'links':body['links'],
                'document_class':'periodic_filing' if periodic else 'event_filing','form':r['payload']['form']},
                period_end=period or None,retrieved_at=now,published_at=r['published_at'],url=r['url'],authority='primary',independence=r['independence'],title=r['title']))
            if r['payload']['form']=='8-K':
                base=r['url'].rsplit('/',1)[0]+'/'
                exhibit_links=[u for u in body['links'] if u.startswith(base) and re.search(r'(ex.?99|exhibit.?99|earn|release)',u,re.I)][:2]
                for url in exhibit_links:
                    doc=document(url)
                    from .source_documents import earnings_metadata
                    metadata,period=earnings_metadata(doc['text'],r['published_at']) if '2.02' in r['payload'].get('items','') else ({},None)
                    rows.append(record(ticker=ticker,source='sec_exhibits',kind='document',payload={'text':doc['text'],**metadata,**{k:doc[k] for k in ('format','page_spans') if k in doc}},
                        period_end=period,retrieved_at=utcnow(),published_at=r['published_at'],url=url,authority='primary',independence=r['independence'],title='8-K earnings release exhibit' if metadata else '8-K release exhibit'))
        except Exception as exc:errors.append('SEC document: '+type(exc).__name__)
    return rows,errors


def document(url):
    from bs4 import BeautifulSoup
    raw=fetch(url)
    if raw.lstrip().startswith(b'%PDF-'):
        from .source_documents import pdf_document
        return pdf_document(raw,url)
    soup=BeautifulSoup(raw,'html.parser')
    link_details={urljoin(url,a['href']):a.get_text(' ',strip=True) for a in soup.find_all('a',href=True)}
    links=list(dict.fromkeys(urljoin(url,a['href']) for a in soup.find_all('a',href=True) if a['href'] and not a['href'].startswith('#')))[:500]
    dates=[]
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    def json_dates(value):
        if isinstance(value,list):
            for child in value: json_dates(child)
        elif isinstance(value,dict):
            date=value.get('datePublished')
            if date:
                try:dates.append(iso(date))
                except (ValueError,TypeError):pass
            if '@graph' in value:json_dates(value['@graph'])
    for node in soup.select('script[type="application/ld+json"]'):
        try:json_dates(json.loads(node.get_text()))
        except (ValueError,TypeError):pass
    for node in soup.select('meta[property="article:published_time"], meta[name="date"]'):
        val=node.get('content') or node.get('datetime')
        try:dates.append(iso(val))
        except (ValueError,TypeError):pass
    if not dates:
        from dateutil.parser import parse
        for node in soup.select('.article-date, .entry-date, time[itemprop="datePublished"]')[:1]:
            try:
                raw_date=node.get('datetime') or node.get_text(' ',strip=True)
                dates.append(iso(parse(raw_date)))
            except (ValueError,TypeError):pass
    for node in soup(['script','style','nav','footer','header','ix:header','ix:hidden','xbrli:context','xbrli:unit']):node.decompose()
    for node in list(soup.find_all(attrs={'hidden':True})):
        if node.parent is not None:node.decompose()
    for node in list(soup.find_all(style=True)):
        if node.parent is not None and re.search(r'display\s*:\s*none',node.get('style',''),re.I):node.decompose()
    text=' '.join(soup.stripped_strings)
    from .source_documents import visible_publication
    visible,excerpt=visible_publication(text)
    return {'text':text[:300_000],'links':links,'link_details':link_details,'published_at':min(dates) if dates else visible,'publication_excerpt':excerpt,'title':title}


def yahoo(ticker, *, factory=None):
    if factory is None:
        import yfinance as yf
        factory=yf.Ticker
    tk=factory(ticker);rows=[];errors=[];base=f'https://finance.yahoo.com/quote/{ticker}/'
    def add(source,kind,payload,**kw):
        now=utcnow();rows.append(record(ticker=ticker,source=source,kind=kind,payload=clean(payload),retrieved_at=now,url=base,**kw))
    info={}
    try:
        info=tk.info or {}
        if info.get('quoteType') not in (None,'EQUITY','ETF'): raise ValueError('Unsupported security type')
        keys=('shortName','longName','sector','industry','website','longBusinessSummary','marketCap','enterpriseValue','forwardPE','trailingPE','priceToSalesTrailing12Months','priceToBook','totalCash','totalDebt','freeCashflow','revenueGrowth','earningsGrowth','grossMargins','operatingMargins','profitMargins','sharesOutstanding','floatShares','sharesShort','sharesShortPriorMonth','shortPercentOfFloat','shortRatio','dateShortInterest','sharesShortPreviousMonthDate','heldPercentInstitutions','heldPercentInsiders','numberOfAnalystOpinions','targetMeanPrice','targetHighPrice','targetLowPrice','regularMarketPrice','regularMarketTime','currency','exchange','quoteType','lastSplitDate','lastSplitFactor')
        add('yahoo_profile','profile',{k:info.get(k) for k in keys},observed_at=utcnow(),clock_quality='observation_only',title='Company snapshot; field periods not independently verified')
        if info.get('regularMarketTime'):
            add('yahoo_price','price',{'price':info.get('regularMarketPrice'),'currency':info.get('currency'),'exchange':info.get('exchange'),'basis':'delayed/reference'},observed_at=info['regularMarketTime'],title='Provider reference quote')
        if info.get('dateShortInterest'):
            add('exchange_short','short_interest',{k:info.get(k) for k in ('sharesShort','sharesShortPriorMonth','shortPercentOfFloat','shortRatio','floatShares','sharesShortPreviousMonthDate')},observed_at=utcnow(),period_end=iso(info['dateShortInterest'])[:10],title='Yahoo-reported short interest; settlement dated',independence='yahoo_short')
    except Exception as exc:errors.append('Yahoo profile: '+type(exc).__name__)
    try:
        history=tk.history(period='2y',auto_adjust=True,actions=False,timeout=15)
        bars=frame_rows(history)
        if bars:
            last=history.index[-1].date().isoformat()
            add('yahoo_price','technical',{'bars':bars,'adjusted':True,'basis':'daily adjusted bars'},observed_at=last,title='Two-year daily price/volume')
    except Exception as exc:errors.append('Yahoo history: '+type(exc).__name__)
    for attr in ('eps_trend','eps_revisions','earnings_estimate','revenue_estimate','recommendations','upgrades_downgrades'):
        try:
            data=frame_rows(getattr(tk,attr))
            if data:add('analyst_ratings' if attr in {'recommendations','upgrades_downgrades'} else 'yahoo_estimates','estimate',{'dataset':attr,'rows':data[:50],'target_period_basis':'provider relative fiscal period; not historical PIT consensus'},observed_at=utcnow(),title=attr)
        except Exception as exc:errors.append('Yahoo '+attr+': '+type(exc).__name__)
    try:
        cal=clean(tk.calendar or {})
        if cal:add('yahoo_estimates','catalyst',{'calendar':cal,'date_status':'provider_estimate'},observed_at=utcnow(),title='Estimated earnings calendar')
    except Exception as exc:errors.append('Yahoo calendar: '+type(exc).__name__)
    try:
        for item in (tk.news or [])[:25]:
            content=item.get('content') or item
            url=(content.get('canonicalUrl') or {}).get('url') or item.get('link') or base
            title=content.get('title','');summary=content.get('summary','')
            pub=content.get('pubDate') or content.get('providerPublishTime')
            if not title:continue
            add('yahoo_news','news',{'headline':title,'summary':summary,'article_url':url,'body_verified':False,'relevance':'direct' if ticker.lower() in (title+' '+summary).lower() or str(info.get('shortName') or ticker).split()[0].lower() in (title+' '+summary).lower() else 'unverified'},published_at=pub,
                independence=(content.get('provider') or {}).get('displayName') or 'yahoo_news',title=title)
    except Exception as exc:errors.append('Yahoo news: '+type(exc).__name__)
    try:
        expiries=list(tk.options or [])
        today=utcnow().date()
        chosen=[e for e in expiries if 7<=(datetime.fromisoformat(e).date()-today).days<=65]
        for expiry in ([chosen[0],chosen[-1]] if len(chosen)>1 else chosen[:1]):
            chain=tk.option_chain(expiry)
            add('options','option',{'expiry':expiry,'calls':frame_rows(chain.calls),'puts':frame_rows(chain.puts),
                'quote_clock':'snapshot; lastTradeDate is trade time, not NBBO time','oi_clock':'provider daily OI; exact session unverified'},observed_at=utcnow(),title=f'Options expiry {expiry}',clock_quality='observation_only')
    except Exception as exc:errors.append('Yahoo options: '+type(exc).__name__)
    return rows,errors


def credential_sources(ticker):
    rows=[];errors=[];now=utcnow()
    key=os.environ.get('FINNHUB_API_KEY')
    if key:
        url='https://finnhub.io/api/v1/company-news'
        try:
            raw=getjson(url,params={'symbol':ticker,'from':(now-timedelta(days=14)).date().isoformat(),'to':now.date().isoformat(),'token':key})
            for n in raw[:60]:
                rows.append(record(ticker=ticker,source='finnhub_news',kind='news',payload={'headline':n.get('headline'),'summary':n.get('summary'),'article_url':n.get('url'),'body_verified':False},
                    retrieved_at=utcnow(),published_at=n.get('datetime'),url=n.get('url') or url,title=n.get('headline',''),independence=n.get('source') or 'finnhub'))
        except Exception as exc:errors.append('Finnhub news: '+type(exc).__name__)
    key=os.environ.get('FMP_API_KEY')
    if key:
        url='https://financialmodelingprep.com/stable/analyst-estimates'
        try:
            raw=getjson(url,params={'symbol':ticker,'period':'quarter','limit':8,'apikey':key})
            if not isinstance(raw,list):raise ValueError('Unexpected estimate response')
            rows.append(record(ticker=ticker,source='fmp_estimates',kind='estimate',payload={'dataset':'analyst_estimates','rows':raw},retrieved_at=utcnow(),observed_at=utcnow(),url=url,title='Period-specific quarterly estimates'))
        except Exception as exc:errors.append('FMP estimates: '+type(exc).__name__)
    return rows,errors


def macro(ticker):
    from advisor.research.ingest.macro_fred import _download, _latest, SERIES
    rows=[];errors=[]
    for series in ('DGS10','BAMLH0A0HYM2','NFCI'):
        try:
            date,value=_latest(_download(series),series)
            rows.append(record(ticker=ticker,source='fred',kind='macro',payload={'series':series,'value':value,'name':SERIES[series]['name'],'vintage':'latest observed; not historical PIT'},
                retrieved_at=utcnow(),observed_at=date,url=f'https://fred.stlouisfed.org/series/{series}',authority='primary',title=SERIES[series]['name']))
        except Exception as exc:errors.append('FRED '+series+': '+type(exc).__name__)
    return rows,errors


def imported(ticker,data):
    """Explicit evidence envelopes for licensed lending/options/alternative feeds."""
    from advisor.intelligence.contract import digest
    path=Path(data)/'intelligence'/'source_inbox'/f'{ticker}.json'
    if not path.exists():return [],[]
    try:
        items=json.loads(path.read_text());out=[]
        for item in items:
            if item.get('ticker')!=ticker:raise ValueError('Source envelope ticker mismatch')
            source=item['source']
            from .catalog import SOURCES
            spec=next(s for s in SOURCES if s.id==source)
            row=record(**{k:v for k,v in item.items() if k not in {'id','temporal'}})
            row['authority']=spec.authority
            row['id']=digest({k:v for k,v in row.items() if k!='id'})[:24]
            out.append(row)
        return out,[]
    except Exception as exc:return [],['Source import rejected: '+type(exc).__name__]
