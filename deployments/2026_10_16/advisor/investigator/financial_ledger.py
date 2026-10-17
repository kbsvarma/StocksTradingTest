"""Source-attributed financial facts and reproducible, period-matched calculations."""
from datetime import date
from .temporal import iso, utcnow
import re

LABELS={'revenue':'Revenue','net_income':'GAAP consolidated net income','op_income':'GAAP operating income',
 'eps_diluted':'GAAP diluted EPS','cash':'Cash and cash equivalents','equity':'Stockholders equity',
 'cfo':'Operating cash flow','capex':'Cash capital expenditures','sbc':'Stock compensation expense',
 'total_debt':'Reported total debt','current_debt':'Current debt','noncurrent_debt':'Noncurrent debt','interest_expense':'Interest expense',
 'shares_outstanding':'Reported common shares outstanding'}


def build(rows,ticker,valuation=None):
    own=[r for r in rows if r.get('ticker')==ticker]
    facts=[r for r in own if r.get('kind')=='fundamental' and r.get('authority')=='primary'
           and r.get('temporal',{}).get('state')!='excluded']
    latest_release=max((r.get('period_end') or '' for r in own if r.get('kind')=='document'
        and r.get('payload',{}).get('document_class') in {'earnings_release','periodic_filing'}
        and r.get('temporal',{}).get('state')=='current'),default='')
    filings={r['payload'].get('accession'):r['url'] for r in own if r.get('kind')=='filing'}
    entries=[];selected={}
    def add(key,label,value,unit,start,end,inputs,formula,definition=''):
        if not isinstance(value,(int,float)):return None
        ident='F'+str(len(entries)+1)
        sources=[]
        for r in inputs:
            url=filings.get(r.get('payload',{}).get('accession')) or r.get('url')
            if url and url not in sources:sources.append(url)
        entries.append({'id':ident,'key':key,'label':label,'value':round(value,6),'unit':unit,
            'period_start':start,'period_end':end,'latest_results_period':latest_release,
            'period_status':'older disclosed period' if latest_release and end and end<latest_release else 'latest disclosed period',
            'formula':formula,'definition':definition,'source_urls':sources,
            'inputs':[{'id':r.get('id'),'value':r.get('payload',{}).get('value',r.get('payload',{}).get('price')),
                'unit':r.get('payload',{}).get('unit',r.get('payload',{}).get('currency')),
                'period_start':r.get('period_start'),'period_end':r.get('period_end') or r.get('observed_at'),
                'tag':r.get('payload',{}).get('tag'),'url':filings.get(r.get('payload',{}).get('accession')) or r.get('url')} for r in inputs]})
        return entries[-1]
    for metric,label in LABELS.items():
        for duration in ('quarter','ytd','annual','instant'):
            eligible=[r for r in facts if r['payload'].get('metric')==metric and r['payload'].get('duration_class')==duration
                and r.get('temporal',{}).get('state')=='current']
            if not eligible and duration=='annual':
                # The latest fiscal year remains useful historical context after
                # the 150-day current-results window. Preserve its actual dates;
                # never relabel it TTM or the latest quarter.
                eligible=[r for r in facts if r['payload'].get('metric')==metric
                    and r['payload'].get('duration_class')=='annual' and r.get('period_end')
                    and r.get('temporal',{}).get('state')=='context_only'
                    and not (set(r.get('temporal',{}).get('reasons',[]))-
                        {'stale_observation','old_measurement_period','superseded_measurement_period'})
                    and 0<=(utcnow().date()-date.fromisoformat(r['period_end'])).days<=550]
            if not eligible:continue
            r=max(eligible,key=lambda r:(r['period_end'],r.get('published_at') or ''))
            selected[metric,duration]=r
            definition=r['payload'].get('aggregation','')
            if r['payload'].get('share_classes'):definition+='; reported share classes: '+str(r['payload']['share_classes'])
            add(metric+'_'+duration,label+' · '+duration,r['payload']['value'],r['payload']['unit'],r.get('period_start'),r['period_end'],[r],'Reported fact',definition)
    for duration in ('quarter','ytd','annual'):
        rev=selected.get(('revenue',duration))
        if rev and rev['payload']['value']>0:
            for metric in ('net_income','op_income'):
                r=selected.get((metric,duration))
                if r and same_window(r,rev):
                    add(metric+'_margin_'+duration,LABELS[metric]+' margin · '+duration,r['payload']['value']/rev['payload']['value']*100,
                        '%',r.get('period_start'),r['period_end'],[r,rev],'income / revenue × 100','Margin percentage, not growth in profit dollars')
        for metric in ('revenue','net_income','op_income'):
            r=selected.get((metric,duration))
            if not r:continue
            priors=[p for p in facts if p['payload'].get('metric')==metric and p['payload'].get('duration_class')==duration
                and p['payload']['unit']==r['payload']['unit'] and p.get('period_start') and r.get('period_start')
                and 350<=(date.fromisoformat(r['period_end'])-date.fromisoformat(p['period_end'])).days<=380
                and abs((date.fromisoformat(r['period_end'])-date.fromisoformat(r['period_start'])).days-
                        (date.fromisoformat(p['period_end'])-date.fromisoformat(p['period_start'])).days)<=12]
            if priors:
                prior=max(priors,key=lambda p:p.get('published_at') or '')
                if prior['payload']['value']>0:
                    add(metric+'_growth_'+duration,LABELS[metric]+' YoY growth · '+duration,
                        (r['payload']['value']/prior['payload']['value']-1)*100,'%',r.get('period_start'),r['period_end'],[r,prior],
                        '(current / comparable prior-year value − 1) × 100','Same-duration year-over-year comparison')
    sector=' '.join(str(r.get('payload',{}).get('industry',''))+' '+str(r.get('payload',{}).get('sic_description','')) for r in own if r.get('kind')=='profile').lower()
    bank=any(w in sector for w in ('bank','insurance','reit','real estate investment'))
    for duration in ('quarter','ytd','annual'):
        cfo=selected.get(('cfo',duration));capex=selected.get(('capex',duration))
        if not bank and cfo and capex and same_window(cfo,capex):
            add('fcf_'+duration,'CFO minus cash capex · '+duration,cfo['payload']['value']-capex['payload']['value'],'USD',cfo.get('period_start'),cfo['period_end'],[cfo,capex],
                'Operating cash flow − cash capex','Cash-flow proxy; leases, acquisitions and SBC require separate analysis')
    total=selected.get(('total_debt','instant'));cash=selected.get(('cash','instant'))
    if total and cash and same_window(total,cash):
        add('net_debt','Reported debt minus cash',total['payload']['value']-cash['payload']['value'],'USD',None,total['period_end'],[total,cash],
            'Reported total debt − cash and cash equivalents','Excludes marketable securities and any leases not included in the source debt measure')
    price_rows=[r for r in own if r.get('kind')=='price' and r.get('temporal',{}).get('state')=='current' and r['payload'].get('currency')=='USD']
    price=max(price_rows,key=lambda r:r.get('observed_at') or '',default=None)
    if price:
        add('price_reference','Dated reference share price',price['payload']['price'],'USD/shares',None,price['observed_at'][:10],[price],
            'Observed provider price','Market reference, not a financial reporting period or price target')
    eps=selected.get(('eps_diluted','annual'))
    profiles=[r['payload'] for r in own if r.get('kind')=='profile']
    split_dates=[iso(p.get('lastSplitDate'))[:10] for p in profiles if p.get('lastSplitDate')]
    split_unresolved=bool(eps and any(d>eps['period_end'] for d in split_dates))
    if price and eps and eps['payload']['value']>0 and not split_unresolved:
        add('pe_fiscal_year','Price / reported fiscal-year GAAP diluted EPS',price['payload']['price']/eps['payload']['value'],'x',
            eps.get('period_start'),eps['period_end'],[price,eps],'Dated share price / reported annual diluted EPS',
            'Historical fiscal-year earnings basis; not a forward multiple or a fair-value verdict. Price as of '+str(price.get('observed_at')))
    if price:
        if bank:
            # A disclosed per-share book measure has an explicit denominator;
            # do not infer tangible common equity from total shareholders equity.
            docs=sorted([r for r in own if r.get('kind')=='document'
                and r.get('authority') in {'primary','issuer_statement'}
                and r.get('temporal',{}).get('state')=='current'
                and r.get('period_end')
                and r.get('payload',{}).get('document_class')=='earnings_release'],
                key=lambda r:(r['period_end'],r.get('published_at') or ''),reverse=True)
            for r in docs:
                text=r['payload'].get('markdown') or r['payload']['text']
                match=re.search(r'tangible book value per share(?:\s*\d+)?\s+of\s+\$([\d,]+(?:\.\d+)?)',text,re.I)
                if not match:continue
                amount=float(match[1].replace(',',''))
                if amount<=0:continue
                observed={**r,'period_start':None,'payload':{'value':amount,'unit':'USD/shares','tag':'disclosed tangible book value per share'}}
                add('tangible_book_per_share','Reported tangible book value per share',amount,'USD/shares',None,r['period_end'],[observed],
                    'Explicit source disclosure',match[0])
                add('price_tangible_book','Price / tangible book value per share',price['payload']['price']/amount,'x',None,r['period_end'],[price,observed],
                    'Dated share price / disclosed tangible book value per share','Not fair value; assess sustainable returns and credit risk. Price as of '+str(price.get('observed_at')))
                break
        # Extract only explicit annual adjusted EPS and guidance phrases, never infer a year from a quarter.
        docs=sorted([r for r in own if r.get('kind')=='document' and r.get('payload',{}).get('document_class')=='earnings_release'
            and r.get('authority') in {'primary','issuer_statement'}
            and r.get('temporal',{}).get('state')=='current'],key=lambda r:r.get('published_at') or '',reverse=True)
        extracted=set()
        for r in docs:
            for block in re.split(r'\n\s*\n',r['payload'].get('markdown') or r['payload']['text']):
                patterns=[('adjusted_eps_ex_gains',r'(?:FY\s*|fiscal year\s*)(20\d{2})\s+(?:non[- ]GAAP|adjusted)\s+(?:diluted\s+)?EPS\s+(?:would be|was|of)\s+\$(\d+(?:\.\d+)?)',bool(re.search(r'excluding.{0,100}(?:gains|items)',block,re.I))),
                          ('adjusted_eps_guidance',r'(?:FY\s*|fiscal year\s*)(20\d{2})[^\n]{0,180}?(?:non[- ]GAAP|adjusted)\s+(?:diluted\s+)?EPS\s+guidance\s+(?:to|of)\s+\$(\d+(?:\.\d+)?)',True)]
                for key,pattern,eligible in patterns:
                    if not eligible:continue
                    m=re.search(pattern,block,re.I)
                    if not m or (key,m[1]) in extracted:continue
                    amount=float(m[2]);extracted.add((key,m[1]))
                    if amount<=0:continue
                    label=('Adjusted EPS excluding specified investment gains' if key.endswith('ex_gains') else 'Management adjusted EPS guidance')+' · FY'+m[1]
                    observed={**r,'payload':{'value':amount,'unit':'USD/shares','tag':'explicit annual EPS phrase'}}
                    fact=add(key,label,amount,'USD/shares',None,None,[observed],'Explicit source disclosure',block[:900])
                    fact['period_label']='FY'+m[1]+(' management guidance' if key.endswith('guidance') else ' adjusted, specified gains excluded')
                    ratio=add('price_'+key,'Price / '+label,price['payload']['price']/amount,'x',None,None,[price,observed],
                        'Dated share price / explicitly disclosed annual EPS','Forward guidance is not realized earnings; adjusted EPS is not GAAP. Price as of '+str(price.get('observed_at')))
                    ratio['period_label']=fact['period_label']

    if valuation:
        lookup={r.get('id'):r for r in own}
        trailing=valuation.get('trailing',{})
        def source_inputs(metric):
            return [lookup[i] for i in metric.get('ids',[]) if i in lookup]
        for key,item in trailing.items():
            if not item or key not in LABELS:continue
            inputs=source_inputs(item)
            if not inputs or len(inputs)!=len(item['ids']):continue
            add('ttm_'+key,LABELS[key]+' · trailing 12 months',item['value'],'USD',None,item['end'],inputs,
                item['method'],'Period-matched fiscal-year and YTD bridge; not annualized quarterly results')
        if not bank and valuation.get('ttm_fcf_proxy') is not None:
            cfo=trailing.get('cfo');capex=trailing.get('capex')
            if cfo and capex and cfo['end']==capex['end']:
                add('ttm_fcf','CFO minus cash capex · trailing 12 months',cfo['value']-capex['value'],'USD',None,cfo['end'],
                    source_inputs(cfo)+source_inputs(capex),'Trailing CFO − trailing cash capex','Proxy; reconcile leases, acquisitions and SBC separately')
        shares=[r for r in facts if r['payload'].get('metric')=='shares_outstanding'
            and r['payload'].get('unit')=='shares' and r.get('temporal',{}).get('state')=='current']
        share=max(shares,key=lambda r:r['period_end'],default=None)
        if price and share and valuation.get('equity_value_proxy') is not None:
            cap=price['payload']['price']*share['payload']['value']
            if abs(cap-valuation['equity_value_proxy'])<=max(1,abs(cap)*1e-6):
                definition=valuation.get('equity_value_basis','')+'; '+valuation.get('equity_value_inputs',{}).get('price_basis','')
                add('equity_value_proxy','Equity value using dated reported shares',cap,'USD',None,share['period_end'],[price,share],
                    'Reference price × dated reported shares outstanding',definition)
                revenue=trailing.get('revenue')
                if not bank and revenue and revenue['value']>0:
                    add('price_sales_proxy','Equity value / trailing sales · dated-share proxy',cap/revenue['value'],'x',None,revenue['end'],
                        [price,share]+source_inputs(revenue),'Reference price × dated reported shares / period-matched trailing revenue',
                        definition+'; Sales multiple is not a profitability or fair-value conclusion.')

    gaps=[]
    if not any(e['key']=='pe_fiscal_year' for e in entries):gaps.append('Positive annual diluted EPS with compatible share-price basis unavailable; no annual P/E computed.')
    if latest_release and not any(e['period_end']==latest_release for e in entries):gaps.append('Latest earnings release is newer than structured financial facts. Older calculations remain explicitly dated; current release extraction is required.')
    if bank:gaps.append('Sector-specific capital, credit, tangible-book or FFO measures require dedicated evidence; industrial FCF is not used.')
    return {'entries':entries,'gaps':gaps,'latest_results_period':latest_release,'price_date':price.get('observed_at') if price else None,
            'price':price['payload']['price'] if price else None,'sector_method':'specialized financial/real-estate business' if bank else 'operating business'}


def same_window(a,b):
    return all(a.get(k)==b.get(k) for k in ('period_start','period_end')) and a['payload']['unit']==b['payload']['unit']


def markdown(ledger):
    lines=['### Financial facts and calculations','Every value below retains its accounting basis and reporting period.','',
           '| Measure | Value | Reporting period | Sources |','|---|---:|---|---|']
    # Show annual/instant facts and derived measures without overwhelming the report with duplicate quarter rows.
    for e in ledger['entries']:
        if e['key'].endswith('_quarter'):continue
        value=f"{e['value']/1e9:,.3f}bn" if e['unit']=='USD' else f"{e['value']:,.2f}{e['unit'] if e['unit'] in {'%','x'} else ''}"
        if e['unit'] in {'USD','USD/shares'}:value='$'+value
        label=e['label']
        sources=' · '.join(f'[Input {i+1}]({url})' for i,url in enumerate(e['source_urls']))
        period=e.get('period_label') or ((e['period_start']+' to ' if e['period_start'] else 'As of ')+str(e['period_end']))
        if e['period_status']=='older disclosed period':period+=' · older than latest release'
        lines.append(f'| {label} | {value} | {period} | {sources} |')
    if ledger.get('price') is not None:lines+=['',f"Price reference: ${ledger['price']:,.2f} · {ledger['price_date']}."]
    lines+=['']+['- '+gap for gap in ledger['gaps']]
    return '\n'.join(lines)
