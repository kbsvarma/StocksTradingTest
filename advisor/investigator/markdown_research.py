"""Bounded, topic-balanced retrieval from dated document Markdown."""
import math
import re
from collections import Counter

TOPICS = {
    'results': 'revenue net income operating income diluted earnings quarter',
    'drivers': 'growth demand segment revenue semiconductor software recurring annualized ARR AI',
    'outlook': 'outlook guidance expects next quarter fiscal year',
    'quality': 'GAAP reconciliation stock based compensation amortization tax gain significant items',
    'cash': 'cash flows operating activities capital expenditures purchases property free cash flow',
    'valuation': 'shares outstanding repurchase diluted dividends',
    'risk': 'customer concentration credit financing guarantees commitments default',
    'execution': 'gross margin mix competition supply capacity power deployment',
    'financing': 'capital funding raise debt equity issuance financing prepayment guarantees',
    'catalysts': 'earnings announcement conference call results released scheduled date upcoming',
    'balance': 'cash debt receivables inventory liabilities capital CET1 charge offs',
}


def to_markdown(soup):
    """Retain source order and financial table columns; do not flatten table cells."""
    from bs4 import BeautifulSoup
    node=BeautifulSoup(str(soup),'html.parser')
    for table in list(node.find_all('table')):
        if table.find('table'):continue  # layout tables must not swallow nested statements
        grid=[]
        for row in table.find_all('tr'):
            cells=[re.sub(r'\s+',' ',c.get_text(' ',strip=True)).replace('|','\\|') for c in row.find_all(['td','th'],recursive=False)]
            if any(cells):grid.append(cells)
        if not grid:continue
        width=max(map(len,grid));grid=[r+['']*(width-len(r)) for r in grid]
        keep=[i for i in range(width) if any(r[i] for r in grid)]
        lines=['| '+' | '.join(r[i] for i in keep)+' |' for r in grid]
        lines.insert(1,'| '+' | '.join('---' for _ in keep)+' |')
        table.replace_with('\n\n'+'\n'.join(lines)+'\n\n')
    for br in node.find_all('br'):br.replace_with('\n')
    for tag in node.find_all(['p','div','h1','h2','h3','h4','li']):
        tag.insert_before('\n\n')
        if tag.name.startswith('h'):tag.insert_before('#'*int(tag.name[1])+' ')
        tag.insert_after('\n\n')
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',node.get_text(' ',strip=False)).strip()


def chunks(text,limit=1800):
    # PDF extraction uses spaces to position columns. Markdown tables already
    # have delimiters; retain line/table structure without wasting retrieval
    # budget on hundreds of alignment spaces between a metric and its value.
    text=re.sub(r'[^\S\n]+',' ',text)
    heading='';out=[]
    for block in re.split(r'\n\s*\n',text):
        block=block.strip()
        if not block:continue
        if block.startswith('#') or (len(block)<150 and '|' not in block and re.search(
            r'guidance|outlook|capital funding|capital investment|footnotes|balance sheets|statements of cash|non-gaap financial',block,re.I)):
            heading=block[:180]
        if block.startswith('|'):
            lines=block.splitlines();head=lines[:min(7,len(lines))];batch=[]
            for line in lines[len(head):]:
                if batch and len('\n'.join(head+batch+[line]))>limit:
                    out.append(heading+'\n'+'\n'.join(head+batch));batch=[]
                batch.append(line)
            if batch or len(lines)<=7:out.append(heading+'\n'+'\n'.join(head+batch))
        else:
            batch=''
            for sent in re.split(r'(?<=[.!?])\s+',block):
                # Preserve sentences, but bound pathological unbroken extracted text.
                for offset in range(0,len(sent),limit):
                    part=sent[offset:offset+limit]
                    if batch and len(batch)+len(part)>limit:out.append(heading+'\n'+batch);batch=''
                    batch+=part+' '
            if batch:out.append(heading+'\n'+batch)
    return [('TABLE SCOPE: deferred tax asset/liability balances, NOT compensation expense or cash flow.\n'+s.strip()
             if re.search(r'deferred (?:income )?tax assets',s,re.I) and '|' in s else s.strip())
            for s in out if len(s.strip())>35]


def retrieve(documents,*,budget=27000,review=False,business_type='general'):
    pool=[]
    for doc in documents:
        for i,text in enumerate(chunks(doc['text'])):
            pool.append({'source_id':doc['source_id'],'period_end':doc.get('period_end'),'document_class':doc.get('document_class'),'published_at':doc.get('published_at'),'section_id':doc['source_id']+'C'+str(i+1),'text':text})
    if not pool:return [],dict.fromkeys(TOPICS,'No readable sections')
    tokenize=lambda x:re.findall(r'[a-z0-9]+',x.lower())
    counts=[Counter(tokenize(c['text'])) for c in pool]
    df=Counter(w for c in counts for w in c)
    topic_queries=dict(TOPICS)
    if business_type=='bank':
        topic_queries.update(valuation='tangible book value per share ROTCE excluding significant items',
            cash='net interest income excluding Markets deposits loans funding liquidity',
            balance='Standardized CET1 capital ratio requirement regulatory net charge offs provisions reserves',
            quality='net income excluding significant items percent growth pretax after tax gains')
    topics=list(topic_queries)
    if review:topics=['risk','quality','financing','execution','balance','drivers','outlook','catalysts','results','cash','valuation']
    selected={};coverage={}
    # Preserve the current release's headline narrative and segment summary before lexical search.
    # Otherwise keyword-dense legal disclaimers can crowd out the actual business results.
    releases=[d for d in documents if d.get('document_class')=='earnings_release']
    if releases:
        current=max(releases,key=lambda d:d.get('published_at') or '')
        anchor_used=0
        release_chunks=[p for p in pool if p['source_id']==current['source_id']]
        mandatory=[p for p in release_chunks if re.search(r'guidance|outlook|capital funding|expects to raise|equity issuance|excluding.{0,80}(?:gains|items)',p['text'],re.I)
                   and not re.search(r'forward-looking statements|uncertainties that could',p['text'],re.I)]
        ordered=mandatory+release_chunks[:22]
        for chunk in ordered:
            if chunk['section_id'] in selected:continue
            if anchor_used+len(chunk['text'])>min(10000,budget//2):break
            selected[chunk['section_id']]=chunk;anchor_used+=len(chunk['text'])
    allowance=(budget-sum(len(c['text']) for c in selected.values()))//len(topics)
    for topic in topics:
        words=tokenize(topic_queries[topic]);ranked=[]
        for i,c in enumerate(counts):
            score=sum(math.log(1+len(pool)/(1+df[w]))*min(c[w],3) for w in words)
            body=pool[i]['text'].lower()
            # Financial topics cannot silently fall back to superseded balance sheets.
            if topic in {'results','cash','balance','quality'}:
                latest=max((d.get('period_end') or '' for d in documents if d.get('document_class') in {'earnings_release','periodic_filing'}),default='')
                if latest and (pool[i].get('period_end') or '')<latest:score*=.03
            if topic in {'outlook','financing'} and pool[i].get('document_class')=='earnings_release':score*=3
            if topic=='quality' and re.search(r'excluding.{0,80}(?:gains|items)',body):score*=8
            if business_type=='bank' and topic=='balance' and 'cet1' in body and 'requirement' in body:score*=15
            if any(term in body for term in ('uncertainties that could','forward-looking statements','should not be considered as a substitute')):score*=.08
            if topic=='quality' and '|' in body and 'stock' in body and 'compensation' in body:
                if 'deferred income tax assets' in body or 'deferred tax assets' in body:score=0
                elif 'net cash from operations' in body or 'operating activities' in body or 'reconcile net income' in body:score*=15
                else:score*=2
            ranked.append((score,i))
        used=0;hits=[]
        for score,i in sorted(ranked,reverse=True):
            if score<=0:break
            chunk=pool[i];size=len(chunk['text'])
            if chunk['section_id'] in selected:
                hits.append(chunk['section_id'])
                if len(hits)>=2:break
                continue
            if size>allowance-used:continue
            hits.append(chunk['section_id']);selected[chunk['section_id']]=chunk;used+=size
            if len(hits)>=2:break
        coverage[topic]=hits or 'No section fits this topic budget; evidence not established'
    return list(selected.values()),coverage
