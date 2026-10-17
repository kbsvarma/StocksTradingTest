"""Discover issuer IR/news pages and dated original releases without a search key."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from .collectors import document
from .temporal import record,utcnow

# Verified navigation seeds, not analysis, claims or hardcoded result dates.
SEEDS={
    'NVDA':['https://nvidianews.nvidia.com/news','https://investor.nvidia.com/financial-info/quarterly-results/default.aspx'],
    'MSFT':['https://www.microsoft.com/en-us/investor/default'],
    'AAPL':['https://investor.apple.com/investor-relations/default.aspx','https://www.apple.com/newsroom/'],
    'GOOGL':['https://abc.xyz/investor/Earnings/default.aspx'],
    'GOOG':['https://abc.xyz/investor/Earnings/default.aspx'],
    'JPM':['https://www.jpmorganchase.com/ir/quarterly-earnings'],
    'JNJ':['https://investor.jnj.com/','https://investor.jnj.com/financials/quarterly-results/default.aspx'],
}


def collect(ticker,rows,data,*,fetcher=document):
    profiles=[r['payload'] for r in rows if r['ticker']==ticker and r['kind']=='profile']
    website=next((p.get('website') for p in profiles if p.get('website')),None)
    roots=list(SEEDS.get(ticker,[]));errors=[];output=[];seen=set();queue=[];labels={}
    # Deployment-supplied issuer routes are URL-only. No model instructions or credentials.
    config=Path(data)/'intelligence'/'issuer_sources.json'
    if config.exists():
        try:roots+=json.loads(config.read_text()).get(ticker,[])[:4]
        except (ValueError,TypeError):errors.append('Issuer route configuration invalid')
    if website and website.startswith('https://'):
        roots.append(website)
    if website and not SEEDS.get(ticker):
        host=(urlparse(website).hostname or '').removeprefix('www.')
        if host:roots.extend(['https://investor.'+host,'https://investors.'+host])
    if not roots:return [],['Issuer website/IR route unavailable']
    company_host=urlparse(website or roots[0]).hostname or ''
    company_host=company_host.removeprefix('www.')
    root_hosts={urlparse(url).hostname for url in roots}
    def rank(url):
        u=(urlparse(url).path+' '+labels.get(url,'')).lower()
        return sum(weight for word,weight in [('announcement',12),('earnings-date',12),('financial-results',8),('earnings',7),('acquire',6),('outlook',6),('quarter',5),('partnership',3),('news',1),('investor',1)] if word in u)
    navigation=[]
    for root in roots[:4]:
        try:
            seen.add(root)
            page=fetcher(root);labels.update(page.get('link_details',{}))
            navigation.extend(u for u in page['links'] if re.search(r'investor|investor relations',u+' '+labels.get(u,''),re.I)
                and (urlparse(u).hostname==company_host or (urlparse(u).hostname or '').endswith('.'+company_host)))
            links=[u for u in page['links'] if re.search(r'(news|press|earnings|financial-results|acquir|collaboration|partnership|quarterly|results)',urlparse(u).path+' '+labels.get(u,''),re.I)]
            queue.extend((u,root) for u in links if urlparse(u).scheme=='https' and not u.lower().endswith(('.zip','.png','.jpg')))
        except Exception as exc:errors.append('Issuer navigation: '+type(exc).__name__)
    for route in list(dict.fromkeys(navigation))[:2]:
        if route in seen:continue
        try:
            seen.add(route);page=fetcher(route);labels.update(page.get('link_details',{}))
            queue.extend((u,route) for u in page['links'] if urlparse(u).scheme=='https'
                and re.search(r'earnings|news-details|financial-results|results|announcement',u+' '+labels.get(u,''),re.I))
        except Exception as exc:errors.append('Issuer IR directory: '+type(exc).__name__)
    # Dedupe navigation, prioritize results and economically meaningful announcements.
    queue=list(dict.fromkeys(queue));queue.sort(key=lambda x:rank(x[0]),reverse=True)
    fetched=0
    for url,parent in queue:
        if url in seen:continue
        leaf=urlparse(url).path.rstrip('/').rsplit('/',1)[-1]
        if leaf in {'','news','search','bios','multimedia','in-the-news','contacts','investors','investor-relations','events','press-releases'}:continue
        host=urlparse(url).hostname or ''
        if not (host==company_host or host.endswith('.'+company_host) or host in root_hosts or host.endswith(('.gcs-web.com','.q4cdn.com'))):continue
        seen.add(url);fetched+=1
        if fetched>8:break
        try:
            doc=fetcher(url)
            call=doc.get('document_class')=='earnings_call'
            if not doc.get('published_at') and not call:continue # directory pages are navigation, not current evidence
            title=doc.get('title','').lower()
            excerpt=doc['text'][:20000].lower()
            financial=any(term in title for term in ('financial results','earnings','quarterly results','full year results','fiscal'))
            guidance=financial and any(term in excerpt for term in ('outlook','guidance','expects revenue','revenue is expected'))
            material=any(term in title for term in ('acquire','acquisition','merger','partnership','dividend','repurchase'))
            dimension='guidance' if guidance else 'fundamentals' if financial else 'catalysts'
            from .source_documents import earnings_metadata
            metadata,period=earnings_metadata(doc['text'],doc['published_at']) if financial else ({},None)
            if call:metadata={k:doc[k] for k in ('document_class','event_basis_excerpt','availability_basis')}
            output.append(record(ticker=ticker,source='issuer_ir',kind='document',payload={'text':doc['text'],'markdown':doc.get('markdown',doc['text']),'discovered_from':parent,'dimension':dimension,'relevance':'direct' if financial or material else 'unverified','publication_basis':'visible document dateline' if doc.get('publication_excerpt') else 'page metadata',**metadata,**{k:doc[k] for k in ('format','page_spans') if k in doc}},
                period_end=period,retrieved_at=utcnow(),observed_at=utcnow() if call else None,event_at=doc.get('event_at'),published_at=doc.get('published_at'),url=url,authority='issuer_statement',independence='issuer:'+ticker,title=doc.get('title') or url.rsplit('/',1)[-1]))
        except Exception as exc:errors.append('Issuer release: '+type(exc).__name__+' ('+(urlparse(url).hostname or '')+urlparse(url).path+')')
    return output,errors
