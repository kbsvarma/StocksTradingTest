import sys,time,json,re,hashlib,concurrent.futures
from pathlib import Path
BASE=Path.home()/'.local/share/advisor/avgo-markdown-experiment';sys.path.insert(0,str(BASE/'deps'))
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from rank_bm25 import BM25Okapi
START=time.monotonic();BASE.mkdir(exist_ok=True);(BASE/'documents').mkdir(exist_ok=True)
r=json.loads(Path('/home/varmak/stockstest/advisor/data/intelligence/investigations/AVGO/avgomarkdown20260908/report.json').read_text())
sources=[]
for e in r['evidence']:
 if e['kind']=='document':sources.append({'url':e['url'],'title':e['title'],'period_end':e['period_end'],'published_at':e['published_at'],'authority':'SEC issuer original','role':'current results' if '08022026x8kxex99' in e['url'] else 'dated background'})
sources.append({'url':'https://www.earningscall.ai/stock/transcript/AVGO-2026-Q3','title':'Q3 FY2026 earnings call transcript','period_end':'2026-08-02','published_at':'2026-09-02','authority':'third-party transcript; not checked against original audio','role':'management commentary and Q&A'})
for i,s in enumerate(sources,1):s['id']=f'D{i}'
def fetch(s):
 t=time.monotonic()
 try:
  res=requests.get(s['url'],headers={'User-Agent':'Advisor research contact research@stocks-terminal.local'},timeout=35);res.raise_for_status()
  html=res.text;(BASE/'documents'/f"{s['id']}.html").write_text(html)
  soup=BeautifulSoup(html,'lxml')
  for node in soup.select('script,style,nav,footer,header,noscript,ix\\:hidden,[hidden]'):node.decompose()
  node=soup.find('article') or soup.find('main') or soup.body or soup
  for cell in node.select('td,th'):
   for br in cell.find_all('br'):br.replace_with(' ')
   for leaf in list(cell.find_all(string=True)):
    leaf.replace_with(re.sub(r'\s+',' ',str(leaf)))
  md=markdownify(str(node),heading_style='ATX',strip=['img','a'])
  md=re.sub(r'\n{3,}','\n\n',md)
  # Remove columns empty throughout a contiguous Markdown table; preserve every nonempty cell.
  blocks=md.split('\n\n');clean=[]
  for b in blocks:
   if b.strip().startswith('|'):
    lines=[x for x in b.splitlines() if x.strip() and re.search(r'[^|\s]',x)];grid=[[c.strip() for c in x.strip().strip('|').split('|')] for x in lines]
    n=max(map(len,grid));grid=[row+['']*(n-len(row)) for row in grid]
    keep=[i for i in range(n) if any(row[i] and not re.fullmatch(r'[: -]+',row[i]) for row in grid)]
    b='\n'.join('| '+' | '.join(row[i] for i in keep)+' |' for row in grid)
   clean.append(b)
  md='\n\n'.join(clean)
  s.update(bytes=len(html),markdown_chars=len(md),table_count=len(node.find_all('table')),sha256=hashlib.sha256(html.encode()).hexdigest(),seconds=round(time.monotonic()-t,3),retrieved_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
  (BASE/'documents'/f"{s['id']}.md").write_text('# '+s['title']+'\n\n'+json.dumps(s)+'\n\n'+md)
  return s,md
 except Exception as e:s['error']=type(e).__name__+': '+str(e);return s,''
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:items=list(pool.map(fetch,sources))
conversion=time.monotonic()-START
chunks=[]
for s,md in items:
 heading=s['title'];blocks=md.split('\n\n');previous=''
 for j,b in enumerate(blocks):
  if not b.strip():continue
  if b.lstrip().startswith('#') or (len(b)<160 and any(x.isalpha() for x in b) and b.upper()==b):heading=b.strip()
  context=heading+'\n'+previous[-350:]
  parts=[]
  if b.lstrip().startswith('|'):
   lines=b.splitlines();head=lines[:min(7,len(lines))];group=[]
   for line in lines[len(head):]:
    group.append(line)
    if sum(map(len,group))>1500:parts.append('\n'.join(head+group));group=[]
   if group or not parts:parts.append('\n'.join(head+group))
   foot='\n'.join(blocks[j+1:j+3])[:400]
   parts=[p+'\nFollowing context: '+foot for p in parts]
  else:
   sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z])',b);group=''
   for sent in sentences:
    if len(group)+len(sent)>1800 and group:parts.append(group);group=''
    group+=sent+' '
   if group:parts.append(group)
  for part in parts:
   if len(part.strip())<50:continue
   chunks.append({'id':s['id']+'C'+str(len(chunks)+1),'source':s['id'],'title':s['title'],'url':s['url'],'period_end':s['period_end'],'published_at':s['published_at'],'authority':s['authority'],'heading':heading,'text':context+'\n'+part})
  previous=b
queries={
 'current_results':'third quarter fiscal 2026 August 2 revenue operating income GAAP non-GAAP earnings diluted',
 'growth_drivers':'AI semiconductor revenue custom accelerators networking infrastructure software VMware',
 'outlook':'fourth quarter fiscal 2027 2028 outlook guidance demand supply',
 'profit_quality':'GAAP non-GAAP amortization stock-based compensation tax nonrecurring',
 'cash':'cash flow operating activities free cash flow purchases property capital expenditures',
 'balance_sheet':'cash debt current portion long-term debt receivables inventory August 2',
 'customer_risk':'customer concentration six customers funding financing credit guarantees commitments power deployment',
 'margin_risk':'gross margin operating margin memory XPU mix fiscal 2027',
 'financing':'convertible notes repurchase dilution proceeds debt maturity',
 'software_quality':'software VMware upfront license revenue subscription bookings recurring',
 'competition':'MediaTek Nvidia GPU custom accelerator competition technology',
 'capital_returns':'dividend repurchases stock-based compensation weighted average shares'}
token=lambda t:re.findall(r'[a-z0-9]+',t.lower());bm=BM25Okapi([token(c['title']+' '+c['text']) for c in chunks]);selected={};hits={}
for topic,q in queries.items():
 scores=bm.get_scores(token(q));scores=[score*({'D4':1.5,'D9':1.4,'D2':0.55,'D1':0.8}.get(c['source'],1)) for score,c in zip(scores,chunks)];ranked=sorted(range(len(chunks)),key=lambda i:scores[i],reverse=True)
 chosen=[]
 for i in ranked:
  c=chunks[i]
  if len(chosen)>=4:break
  if scores[i]<=0:continue
  if topic in ['outlook','margin_risk','software_quality','competition'] and c['source']!='D9':continue
  if topic=='customer_risk' and c['source'] not in ['D1','D9']:continue
  if topic in ['current_results','cash','profit_quality','balance_sheet'] and '08022026x8kxex99' not in c['url']:continue
  if len(c['text'])>12000:continue
  chosen.append(c['id']);selected[c['id']]=c
 hits[topic]=chosen
# Bound total retrieved evidence, preserving each topic's best result before second/third hits.
ordered=[];seen=set()
for level in range(4):
 for topic,ids in hits.items():
  if len(ids)>level and ids[level] not in seen:
   seen.add(ids[level]);ordered.append(selected[ids[level]])
kept=[];size=0
for c in ordered:
 if size+len(c['text'])>65000:continue
 kept.append(c);size+=len(c['text'])
packet={'as_of':'2026-09-07 US Eastern','sources':sources,'market_reference':{'price':r['analysis']['valuation']['equity_value_inputs']['reference_price'],'price_date':r['analysis']['valuation']['price_date'],'note':'Old company-facts valuation deliberately excluded: it ends May 3, before the latest earnings release.'},'retrieved_sections':kept,'retrieval_hits':hits}
(BASE/'packet.json').write_text(json.dumps(packet,indent=2));(BASE/'chunks.json').write_text(json.dumps(chunks));(BASE/'manifest.json').write_text(json.dumps(sources,indent=2));(BASE/'timings.json').write_text(json.dumps({'experiment_started_utc':'2026-09-08T00:25:08Z','fetch_convert_seconds':round(conversion,3),'chunk_retrieve_seconds':round(time.monotonic()-START-conversion,3),'chunks':len(chunks),'retrieved_chunks':len(kept),'retrieved_chars':size},indent=2))
print((BASE/'timings.json').read_text());print(json.dumps([{k:s.get(k) for k in ['id','title','markdown_chars','table_count','error']} for s in sources],indent=2))
