"""Read a bounded set of current company news leads; search snippets are not treated as article evidence."""
from concurrent.futures import ThreadPoolExecutor
import re
from .collectors import document
from .temporal import record,utcnow,assess


def collect(ticker,rows,*,reader=document):
    now=utcnow()
    # Headlines are discovery leads, not evidence. They become evidence only
    # after fetching the article below; requiring body_verified here would
    # prevent the reader from ever opening the provider's discovery leads.
    leads=[r for r in rows if r.get('ticker')==ticker and r.get('kind')=='news'
        and r['payload'].get('relevance')=='direct'
        and not (set(assess(r,now)['reasons'])-{'headline_discovery_only'})]
    leads=sorted(leads,key=lambda r:r.get('published_at') or '',reverse=True)
    seen=set();chosen=[]
    for r in leads:
        url=r['payload'].get('article_url')
        if url and url not in seen:chosen.append(r);seen.add(url)
        if len(chosen)>=2:break
    names=[r['payload'].get('shortName') or r['payload'].get('name') for r in rows if r.get('kind')=='profile' and r.get('ticker')==ticker]
    words=[ticker.lower()]+[str(n).split()[0].lower() for n in names if n]
    def read(r):
        url=r['payload']['article_url']
        try:
            d=reader(url)
            if len(d.get('text',''))<500 or not any(re.search(r'\b'+re.escape(w)+r'\b',d['text'][:15000],re.I) for w in words):
                return None,'Current news: full relevant article text unavailable'
            return record(ticker=ticker,source='current_article',kind='document',authority='secondary',
                title=d.get('title') or r['title'],url=url,published_at=d.get('published_at') or r.get('published_at'),retrieved_at=utcnow(),
                payload={'document_class':'news_article','text':d['text'],'markdown':d.get('markdown') or d['text'],
                    'publication_basis':'publisher metadata' if d.get('published_at') else 'aggregator timestamp',
                    'relevance':'direct','dimension':'catalysts','source_role':'Third-party reporting; attribute claims and distinguish from issuer disclosures'}),None
        except Exception as exc:return None,'Current news: '+type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(read,chosen))
    return [r for r,e in result if r],[e for r,e in result if e]
