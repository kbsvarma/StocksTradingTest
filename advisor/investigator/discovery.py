"""Public search discovery with relevance filtering; snippets are never evidence."""
import re
from urllib.parse import urlparse

STOP=set('a an the and or for of to in on with from by as at is are was were latest recent stock stocks share shares price news results financial company corporation inc corp limited plc reporting period quarter quarterly year fiscal calendar'.split())


def relevant(query,item):
    url=item.get('url','');p=urlparse(url);path=p.path.lower().rstrip('/')
    if p.scheme!='https' or not path or path in {'/home','/home/default.aspx','/download','/en-us'}:return False
    if path.startswith(('/quote/','/finance/quote/','/market-activity/stocks/')):return False
    words={w for w in re.findall(r'[a-z]{3,}',query.lower()) if w not in STOP}
    body=' '.join([item.get('title') or '',item.get('snippet') or '',p.hostname or '',path]).lower()
    matches={w for w in words if re.search(r'\b'+re.escape(w)+r'\b',body)}
    return len(matches)>=min(2,len(words)) and bool(matches)


def search(query,*,client=None):
    from ddgs import DDGS
    client=client or DDGS(timeout=15)
    found=client.text(query,region='us-en',max_results=10,backend='brave')
    rows=[{'url':r['href'],'title':r.get('title',''),'snippet':r.get('body',''),'discovery_provider':'DDGS Brave'} for r in found]
    rows=[r for r in rows if relevant(query,r)]
    if not rows:raise RuntimeError('Search returned no relevant document pages')
    def rank(row):
        host=urlparse(row['url']).hostname or ''
        return (host.endswith('.gov'),any(t in row['title'].lower() for t in ('earnings','financial results','guidance','quarter')))
    return sorted(rows,key=rank,reverse=True)[:6]
