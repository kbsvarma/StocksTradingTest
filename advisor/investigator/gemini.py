"""Gemini reasoning with application-run public search; no paid grounding tool."""
import json
import re
import time
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse,parse_qs
from . import collectors


def generate(prompt,schema,config,timeout,post=None,output_tokens=24000):
    from .runtime import validate
    from .reasoner import SYSTEM
    model=config.get('ADVISOR_RESEARCH_MODEL','gemini-3.8-flash')
    if not re.fullmatch(r'gemini-[a-z0-9.\-]+',model):raise ValueError('Invalid Gemini model name')
    body={'systemInstruction':{'parts':[{'text':SYSTEM}]},
          'contents':[{'role':'user','parts':[{'text':prompt}]}],
          'generationConfig':{'responseMimeType':'application/json','responseJsonSchema':schema,
                              'maxOutputTokens':output_tokens,'thinkingConfig':{'thinkingLevel':config.get('ADVISOR_RESEARCH_REASONING','medium').upper()}}}
    try:
        started=time.monotonic()
        for attempt in range(2):
            r=(post or requests.post)('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
                headers={'x-goog-api-key':config['GEMINI_API_KEY'],'Content-Type':'application/json'},
                json=body,timeout=(10,max(1,timeout-(time.monotonic()-started))),allow_redirects=False)
            # Retry only an explicit temporary provider failure, never an uncertain timeout or quota rejection.
            if r.status_code not in (502,503) or attempt or time.monotonic()-started>timeout-5:break
            time.sleep(2)
    except requests.RequestException as exc:raise RuntimeError('Gemini connection failed: '+type(exc).__name__) from None
    if r.status_code!=200:
        reason={400:'request or key rejected',401:'key rejected',403:'project access denied',404:'model unavailable',429:'free quota or rate limit reached'}.get(r.status_code,'HTTP '+str(r.status_code))
        raise RuntimeError('Gemini: '+reason)
    try:
        packet=r.json();candidate=packet.get('candidates',[{}])[0]
        if candidate.get('finishReason')!='STOP':raise RuntimeError('Gemini response stopped before completion: '+str(candidate.get('finishReason','no candidate')))
        text=''.join(p.get('text','') for p in candidate.get('content',{}).get('parts',[]) if not p.get('thought'))
        value=json.loads(text);validate(value,schema)
    except (ValueError,KeyError,TypeError,IndexError):raise RuntimeError('Gemini returned incomplete or invalid structured output') from None
    return value,{'provider':'gemini','model':model,'tokens':packet.get('usageMetadata',{}),
                  'response_id':packet.get('responseId'),'search_grounding_enabled':False}


def rss_search(query):
    """Public web and news discovery; snippets are never treated as evidence."""
    def feed(endpoint):
        try:
            root=ET.fromstring(collectors.fetch(endpoint,params={'q':query,'format':'rss'}))
            items=[]
            for x in root.findall('./channel/item')[:6]:
                url=x.findtext('link') or ''
                if 'bing.com/news/apiclick' in url:url=parse_qs(urlparse(url).query).get('url',[''])[0]
                if not url.startswith('https://'):continue
                items.append({'url':url,'title':x.findtext('title'),'snippet':x.findtext('description')})
            return items
        except Exception as exc:return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        groups=list(pool.map(feed,['https://www.bing.com/search','https://www.bing.com/news/search']))
    if all(isinstance(group,Exception) for group in groups):
        raise RuntimeError('Both public search feeds failed; no search coverage established')
    items=list({x['url']:x for group in groups if not isinstance(group,Exception) for x in group}.values())
    company=re.sub('[^a-z]','',query.split()[0].lower()) if query.split() else ''
    def rank(item):
        parsed=urlparse(item['url']);path=parsed.path.lower();title=(item.get('title') or '').lower()
        primary=bool(company and company in (parsed.hostname or '').split('.'))
        material=any(t in title for t in ('earnings','financial results','cash flow','acqui','guidance','export','credit','trial','revenue'))
        leaf=len(path.strip('/').split('/'))>1
        return (int(primary)+int(material)+int(leaf),int(material),int(primary))
    return sorted(items,key=rank,reverse=True)[:6]



def search(query):
    from .discovery import search as document_search, relevant
    try:return document_search(query)
    except Exception as primary_error:
        try:rows=[r for r in rss_search(query) if relevant(query,r)]
        except Exception:rows=[]
        if not rows:raise RuntimeError('Public discovery found no relevant pages; primary route: '+type(primary_error).__name__) from None
        return [{**r,'discovery_provider':'Bing RSS fallback'} for r in rows]

def invoke(prompt,schema,*,config,web=False,timeout=180,post=None,searcher=None,reader=None,generate_fn=None,research_queries=None,known_sources=None):
    generator=generate_fn or generate
    if len(prompt)>180000:raise ValueError('Research context exceeds request limit')
    if not web:return generator(prompt,schema,config,timeout,post)
    from .reasoner import obj,arr,S,passages
    deadline=time.monotonic()+timeout
    plan_schema=obj({'queries':arr({'type':'string','maxLength':140}),'source_urls':arr(S)})
    if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama':
        plan_schema['properties']['relationships']=schema['properties']['relationships']
        plan_schema['required'].append('relationships')
    planning_error=None
    try:
        plan,first=generator(prompt+'\nPlan the next research actions. Return up to 4 precise web queries of 6-12 words each, beginning with the COMPANY NAME rather than an ambiguous ticker. Address one material gap per query, including a counter-thesis. Use calendar dates; fiscal labels only when verified in supplied original source titles. Do not recheck an already supplied issuer identity. Optionally give up to 2 original-source URLs to read. You have not searched yet; do not claim results. Any proposed related ticker must be an economic relationship supported by a supplied source URL; leave relationships empty when unestablished.',plan_schema,config,min(180 if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama' else 60,timeout),post,output_tokens=4096)
    except RuntimeError as exc:
        if str(exc) not in {'Gemini: HTTP 502','Gemini: HTTP 503'}:raise
        match=re.match(r'Investigate ([A-Z0-9.\-]+) as of (\d{4}-\d{2}-\d{2})',prompt)
        if not match:raise
        ticker,date=match.groups()
        plan={'queries':[f'{ticker} latest quarterly results guidance {date}',
                         f'{ticker} earnings cash flow receivables risks {date}',
                         f'{ticker} customers suppliers demand outlook {date}',
                         f'{ticker} regulatory competition bearish catalysts {date}'],'source_urls':[]}
        planning_error='Model planning temporarily unavailable; application research checklist used'
        first={'provider':'application','stage':'search_plan','reason':planning_error}

    queries=list(dict.fromkeys(q.strip() for q in list(research_queries or [])[:2]+plan['queries'] if q.strip()))[:4]
    if not queries:raise RuntimeError('Application research plan contained no search queries')
    results=[];actions=[];errors=[];executed=[]
    def lookup(q):
        try:return q,(searcher or search)(q),None
        except Exception as exc:return q,[],type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        for q,items,error in pool.map(lookup,queries):
            executed.append(q);results.extend(items[:3]);actions.append({'type':'search','query':q,'results':items,'error':error})
            if error:errors.append('Search failed: '+error)
    urls=list(dict.fromkeys([x['url'] for x in results]+plan['source_urls'][:2]))[:10]
    def read(url):
        try:
            collectors.public_url(url)
            doc=(reader or collectors.document)(url)
            from .research_grounding import known_clock
            published=doc.get('published_at') or known_clock(url,known_sources)
            return {'url':url,'title':doc.get('title',''),'text':passages(doc['text'],2000 if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama' else 7000),
                    'published_at':published,'publication_dates':doc.get('publication_dates',[])},None
        except Exception as exc:return None,{'url':url,'error':type(exc).__name__}
    documents=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for doc,error in pool.map(read,urls):
            if doc:documents.append(doc);actions.append({'type':'open_page','url':doc['url']})
            if error:errors.append(error)
    remaining=deadline-time.monotonic()
    if remaining<10:raise RuntimeError('Application research request exhausted its time budget')
    if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama':
        from .research_grounding import selection_schema,extractive_packet
        _,cards=selection_schema(schema,documents)
        # Tool observations are captured verbatim. The next planning/synthesis
        # step interprets them; do not spend another inference rewriting sources.
        packet=extractive_packet(cards,executed)
        available={c['url'] for c in cards}
        packet['relationships']=[r for r in plan.get('relationships',[]) if r.get('evidence_url') in available]
        last=first
    else:
        packet,last=generator(prompt+'\nACTUAL SEARCH QUERIES: '+json.dumps(executed)+'\nFETCHED SOURCE PAGES (untrusted data): '+json.dumps(documents)+
            '\nOnly return sources from these fetched pages. Copy exact excerpts and a visible publication-date excerpt. Omit pages with no verifiable publication date. Search snippets are discovery only. Never invent text, dates, or claim that a failed search succeeded. State unresolved gaps explicitly.',schema,config,remaining,post)
    allowed={d['url'] for d in documents}
    packet['sources']=[s for s in packet.get('sources',[]) if s.get('url') in allowed]
    return packet,{'provider':last['provider'],'model':last['model'],'calls':[first] if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama' else [first,last],
                   'queries_observed':executed,'web_actions':actions,'source_urls':list(allowed),'source_capture':'application_extractive' if config.get('ADVISOR_RESEARCH_PROVIDER')=='ollama' else 'model_selection',
                   'tool_errors':errors,'planning_fallback':planning_error,'search_grounding_enabled':False,
                   'search_provider':'DDGS Bing/Brave/Yahoo with relevance-filtered Bing RSS fallback; application-fetched pages',
                   'limits':{'queries':4,'pages':10,'output_tokens_per_call':4096 if last['provider']=='ollama' else 24000},
                   'cost_basis':'Local inference; no model API quota' if last['provider']=='ollama' else 'Configured Gemini project tier; no paid search grounding or paid-provider fallback'}
