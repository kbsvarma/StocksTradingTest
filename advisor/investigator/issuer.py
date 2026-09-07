"""Discover issuer IR/news pages and dated original releases without a search key."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from .collectors import document
from .temporal import record,utcnow

# Verified navigation seeds, not analysis, claims or hardcoded result dates.
SEEDS={'NVDA':['https://nvidianews.nvidia.com/news','https://investor.nvidia.com/financial-info/quarterly-results/default.aspx']}


def collect(ticker,rows,data,*,fetcher=document):
    profiles=[r['payload'] for r in rows if r['ticker']==ticker and r['kind']=='profile']
    website=next((p.get('website') for p in profiles if p.get('website')),None)
    roots=list(SEEDS.get(ticker,[]));errors=[];output=[];seen=set();queue=[]
    # Deployment-supplied issuer routes are URL-only. No model instructions or credentials.
    config=Path(data)/'intelligence'/'issuer_sources.json'
    if config.exists():
        try:roots+=json.loads(config.read_text()).get(ticker,[])[:4]
        except (ValueError,TypeError):errors.append('Issuer route configuration invalid')
    if website and website.startswith('https://'):
        roots.append(website)
    if not roots:return [],['Issuer website/IR route unavailable']
    company_host=urlparse(website or roots[0]).hostname or ''
    company_host=company_host.removeprefix('www.')
    def rank(url):
        u=url.lower()
        return sum(weight for word,weight in [('financial-results',8),('earnings',7),('acquire',6),('outlook',6),('quarter',5),('partnership',3),('news',1),('investor',1)] if word in u)
    for root in roots[:4]:
        try:
            page=fetcher(root);seen.add(root)
            links=[u for u in page['links'] if re.search(r'(news|press|earnings|investor|financial-results|acquir|collaboration|partnership|outlook)',u,re.I)]
            queue.extend((u,root) for u in links if urlparse(u).scheme=='https' and not u.lower().endswith(('.pdf','.zip','.png','.jpg')))
        except Exception as exc:errors.append('Issuer navigation: '+type(exc).__name__)
    # Dedupe navigation, prioritize results and economically meaningful announcements.
    queue=list(dict.fromkeys(queue));queue.sort(key=lambda x:rank(x[0]),reverse=True)
    fetched=0
    for url,parent in queue:
        if url in seen:continue
        leaf=urlparse(url).path.rstrip('/').rsplit('/',1)[-1]
        if leaf in {'','news','search','bios','multimedia','in-the-news','contacts','investors','investor-relations','events','press-releases'}:continue
        host=urlparse(url).hostname or ''
        if not (host==company_host or host.endswith('.'+company_host) or 'investor' in host or host.endswith('.gcs-web.com')):continue
        seen.add(url);fetched+=1
        if fetched>8:break
        try:
            doc=fetcher(url)
            if not doc.get('published_at'):continue # directory pages are navigation, not current evidence
            output.append(record(ticker=ticker,source='issuer_ir',kind='document',payload={'text':doc['text'],'discovered_from':parent,'dimension':'guidance','publication_basis':'page metadata'},
                retrieved_at=utcnow(),published_at=doc['published_at'],url=url,authority='issuer_statement',independence='issuer:'+ticker,title=doc.get('title') or url.rsplit('/',1)[-1]))
        except Exception as exc:errors.append('Issuer release: '+type(exc).__name__)
    return output,errors
