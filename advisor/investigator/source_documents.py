"""Source-document extraction with explicit reporting-period evidence."""
import io
import re

MONTH=r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
DATE=MONTH+r'\s+\d{1,2},?\s+20\d{2}'


def earnings_metadata(text,published_at):
    if not published_at:return {},None
    from dateutil.parser import parse
    cutoff=parse(str(published_at)).date()
    matches=[]
    for match in re.finditer(r'\b(?:quarter|months|year)\b[^.;]{0,100}?\bended\s+('+DATE+r')',text[:15000],re.I):
        try:period=parse(match.group(1)).date()
        except ValueError:continue
        if period<=cutoff:matches.append((period,match.group(0)))
    # Calendar-quarter headers are usable only when their exact quarter-end date
    # also appears in the document. Fiscal-quarter numbers never imply dates.
    header=re.search(r'\b(first|second|third|fourth)[ -]quarter\s+(20\d{2})\s+(?:results|net income)',text[:2000],re.I)
    if header and not re.search(r'\bfiscal\b',text[:2000],re.I):
        ends={'first':'March 31','second':'June 30','third':'September 30','fourth':'December 31'}
        date=ends[header.group(1).lower()]+', '+header.group(2)
        if date in text:
            period=parse(date).date()
            if period<=cutoff:matches.append((period,header.group(0)))
    if not matches:return {},None
    period,excerpt=max(matches,key=lambda item:item[0])
    return {'document_class':'earnings_release','period_basis_excerpt':excerpt},period.isoformat()



def visible_publication(text):
    """Only explicit datelines or a release's 'call today' statement date it."""
    from dateutil.parser import parse
    patterns=[r'\b[A-Z][A-Z .,&()]{2,45}(?:,\s*[A-Z][a-z.]+)?\s*[-—–]\s*('+DATE+r')\s*[-—–]',
              r'conference\s+call\s+today,\s*('+DATE+r')']
    for pattern in patterns:
        match=re.search(pattern,text,flags=re.I if pattern.startswith('conference') else 0)
        if match:return parse(match.group(1)).date().isoformat(),match.group(0)
    return None,None

def pdf_document(raw,url):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw),strict=False)
    if reader.is_encrypted:raise ValueError('Encrypted issuer PDF cannot be read')
    if len(reader.pages)>100:raise ValueError('Issuer PDF exceeds 100-page extraction limit')
    texts=[];spans=[];size=0
    for number,page in enumerate(reader.pages,1):
        text=page.extract_text(extraction_mode='layout') or ''
        if size+len(text)>300000:break
        if texts:size+=2
        spans.append({'page':number,'start':size,'end':size+len(text)})
        texts.append(text);size+=len(text)
    text='\n\n'.join(texts)
    if len(text.strip())<40:raise ValueError('Issuer PDF has no usable text; scanned pages require OCR')
    # File creation/modification metadata is not proof of publication.
    published=None;publication_excerpt=None
    from dateutil.parser import parse
    for line in text[:1800].splitlines():
        match=re.fullmatch(r'\s*(?:[A-Z][A-Z .,&()-]{2,60}[-—–,]\s*)?('+DATE+r')\s*(?:[-—–].*)?',line)
        if match:
            published=parse(match.group(1)).date().isoformat();publication_excerpt=line.strip();break
    if not published:published,publication_excerpt=visible_publication(text)
    title=str((reader.metadata or {}).get('/Title') or url.rsplit('/',1)[-1])
    return {'text':text,'links':[],'title':title,'published_at':published,'publication_excerpt':publication_excerpt,
            'format':'pdf','page_spans':spans,'extraction':'pypdf layout text; table headers and columns require interpretation','pages_extracted':len(spans),'pages_total':len(reader.pages)}
