"""Derive missing fiscal Q4 flow metrics, preserving both source observations."""
from datetime import date,timedelta
from .temporal import record,assess

FLOWS={'revenue','net_income','gross_profit','op_income','cfo','capex','sbc','interest_expense','cost_of_revenue'}


def derive(rows,as_of):
    facts=[r for r in rows if r['kind']=='fundamental' and r['payload']['metric'] in FLOWS and assess(r,as_of)['state']!='excluded']
    out=[]
    for annual in facts:
        p=annual['payload']
        if p['duration_class']!='annual' or not annual.get('period_start'):continue
        candidates=[r for r in facts if r['ticker']==annual['ticker'] and r['payload']['metric']==p['metric']
                    and r['payload']['unit']==p['unit'] and r['payload'].get('tag')==p.get('tag')
                    and r['period_start']==annual['period_start'] and r['payload']['duration_class']=='ytd'
                    and 70<=(date.fromisoformat(annual['period_end'])-date.fromisoformat(r['period_end'])).days<=110]
        if not candidates:continue
        ytd=max(candidates,key=lambda r:r.get('published_at') or '')
        start=(date.fromisoformat(ytd['period_end'])+timedelta(days=1)).isoformat()
        if any(r['ticker']==annual['ticker'] and r['payload']['metric']==p['metric'] and r['payload']['unit']==p['unit'] and r.get('period_start')==start and r['period_end']==annual['period_end'] for r in facts):continue
        if not annual.get('published_at') or not ytd.get('published_at'):continue
        out.append(record(ticker=annual['ticker'],source='sec_facts',kind='fundamental',
            payload={**p,'value':p['value']-ytd['payload']['value'],'duration_class':'quarter',
                     'derivation':'reported fiscal year minus matching nine-month YTD',
                     'input_ids':[annual['id'],ytd['id']]},
            retrieved_at=max(annual['retrieved_at'],ytd['retrieved_at']),
            published_at=max(annual['published_at'],ytd['published_at']),
            period_start=start,period_end=annual['period_end'],url=annual['url'],authority='primary',
            title=f"Derived Q4 {p['metric']} {start} to {annual['period_end']}"))
    return rows+out
