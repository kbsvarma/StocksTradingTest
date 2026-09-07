"""Bind research selections to application-read passages and verified source clocks."""
import copy
from urllib.parse import urlparse


def known_clock(url,sources):
    for source in sources or []:
        if source.get('url')==url and source.get('published_at'):return source['published_at']
    target=urlparse(url)
    if target.hostname not in {'www.sec.gov','sec.gov'} or not target.path.startswith('/Archives/edgar/data/'):return None
    accession=target.path.rsplit('/',1)[0]
    for source in sources or []:
        parsed=urlparse(source.get('url',''))
        if parsed.hostname in {'www.sec.gov','sec.gov'} and parsed.path.rsplit('/',1)[0]==accession and source.get('published_at'):
            return source['published_at']
    return None


def selection_schema(base,documents):
    from .reasoner import document_quote_options,obj,S
    from .temporal import iso
    cards=[]
    for doc in documents:
        if not doc.get('published_at'):continue
        try:published=iso(doc['published_at'])
        except (ValueError,TypeError):continue
        quotes=[]
        for quote in document_quote_options({'payload':{'text':doc['text']}}):
            if len(quote)>500:quote=quote[:500].rsplit(' ',1)[0]
            if len(quote)>=20:quotes.append(quote)
        if not quotes:continue
        cards.append({'source_id':f'R{len(cards)+1:02d}','url':doc['url'],'title':doc.get('title',''),
            'published_at':published,'passages':{f'P{n:02d}':q for n,q in enumerate(quotes,1)}})
    schema=copy.deepcopy(base)
    target=schema['properties']['sources']
    dimensions=base['properties']['sources']['items']['properties']['dimension']
    if cards:
        target['items']={'oneOf':[obj({'source_id':{'type':'string','enum':[c['source_id']]},
            'passage_id':{'type':'string','enum':list(c['passages'])},'dimension':dimensions,
            'why_material':{'type':'string','maxLength':600}}) for c in cards]}
        target['maxItems']=min(6,len(cards))
    else:target['maxItems']=0
    return schema,cards


def expand(packet,cards):
    lookup={c['source_id']:c for c in cards};result=copy.deepcopy(packet);sources=[]
    for source in packet.get('sources',[]):
        card=lookup.get(source.get('source_id'))
        if not card or source.get('passage_id') not in card['passages']:raise ValueError('Research selected an unknown source passage')
        sources.append({'url':card['url'],'title':card['title'],'published_at':card['published_at'],
            'event_at':'','date_excerpt':'','excerpt':card['passages'][source['passage_id']],
            'dimension':source['dimension'],'why_material':source['why_material']})
    result['sources']=sources
    return result


def extractive_packet(cards,queries):
    """Preserve tool observations; interpretation belongs to synthesis/replanning."""
    import re
    from .discovery import STOP
    terms={w for q in queries for w in re.findall(r'[a-z]{3,}',q.lower()) if w not in STOP}
    selections=[]
    for card in cards[:10]:
        passage=max(card['passages'],key=lambda p:sum(bool(re.search(r'\b'+re.escape(w)+r'\b',card['passages'][p].lower())) for w in terms))
        body=card['passages'][passage].lower()
        dimension='accounting' if any(w in body for w in ('cash flow','receivable','operating margin','net income')) else 'guidance' if any(w in body for w in ('guidance','outlook','expects')) else 'fundamentals'
        selections.append({'source_id':card['source_id'],'passage_id':passage,'dimension':dimension,
            'why_material':'Retrieved for the current research questions; this excerpt is an observation, not an endorsed causal conclusion.'})
    return expand({'sources':selections,'queries_run':list(queries),'relationships':[],
                   'unresolved':['Interpret these newly retrieved disclosures and investigate any remaining decision-changing premise.'] if selections else ['No new dated source was established by this search round.']},cards)
