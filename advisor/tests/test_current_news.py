from datetime import timedelta

from advisor.investigator.current_news import collect
from advisor.investigator.temporal import record, utcnow, assess


def lead(url='https://example.com/article', age=0, relevance='direct'):
    now=utcnow()
    return record(ticker='TEST',source='yahoo_news',kind='news',retrieved_at=now,
        published_at=now-timedelta(days=age),title='Test company update',url=url,
        payload={'article_url':url,'body_verified':False,'relevance':relevance})


def test_discovery_lead_is_read_before_becoming_article_evidence():
    original=lead()
    assert assess(original,utcnow())['state']=='context_only'
    calls=[]
    def reader(url):
        calls.append(url)
        return {'title':'Full report','text':'TEST company financial development. '*30}
    rows,errors=collect('TEST',[original,original],reader=reader)
    assert calls==[original['url']]
    assert not errors and len(rows)==1
    assert rows[0]['authority']=='secondary'
    assert rows[0]['payload']['publication_basis']=='aggregator timestamp'
    assert assess(rows[0],utcnow())['state']=='current'


def test_stale_future_and_unrelated_leads_are_not_read():
    def reader(url):
        raise AssertionError('Ineligible lead fetched')
    assert collect('TEST',[lead(age=10),lead(age=-2),lead(relevance='unverified')],reader=reader)==([],[])


def test_failed_or_irrelevant_articles_do_not_become_evidence():
    rows,errors=collect('TEST',[lead()],reader=lambda url:{'text':'Another company. '*50})
    assert not rows and errors
    def fail(url):raise OSError('unavailable')
    rows,errors=collect('TEST',[lead()],reader=fail)
    assert not rows and errors==['Current news: OSError']


def test_article_reader_is_bounded_and_keeps_newest_leads():
    leads=[lead('https://example.com/'+str(i),age=i) for i in range(4)]
    seen=[]
    def reader(url):
        seen.append(url)
        return {'text':'TEST company update. '*40}
    rows,errors=collect('TEST',list(reversed(leads)),reader=reader)
    assert len(rows)==2 and not errors
    assert set(seen)=={leads[0]['url'],leads[1]['url']}
