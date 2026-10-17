"""Change detection separates new observations from genuinely changed source content."""
from advisor.intelligence.contract import digest


def identity(row):
    p=row['payload']
    return (row['ticker'],row['source'],row['kind'],p.get('metric'),row.get('period_start'),row.get('period_end'),
            p.get('dataset'),p.get('expiry'),row['url'] if row['kind'] in {'document','news','filing'} else '')


def content(row):
    return digest(row['payload'])


def compare(rows,previous):
    if not previous:return {'previous_as_of':None,'status':'first_investigation','changed':[],
                            'interpretation':'First observed by this investigator does not mean new to the market.'}
    prior={identity(r):r for r in previous.get('evidence',[])};changes=[]
    for r in rows:
        if r['temporal']['state']!='current':continue
        old=prior.get(identity(r))
        if old is None:kind='newly_observed'
        elif content(old)!=content(r):kind='source_content_changed'
        else:continue
        changes.append({'source_id':r['id'],'title':r['title'],'kind':kind,'source':r['source'],
                        'published_at':r.get('published_at'),'period_end':r.get('period_end'),
                        'previous_source_id':old['id'] if old else None})
    return {'previous_as_of':previous['as_of'],'status':'compared','changed':changes,
            'interpretation':'Retrieval-time changes alone do not count. Newly observed means new to this report history; market novelty requires the underlying event and publication history.'}
