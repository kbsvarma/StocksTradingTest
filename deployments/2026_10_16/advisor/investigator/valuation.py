"""Period-reconciled trailing cash flows and explicit market-implied expectations."""
from datetime import datetime,timedelta
from advisor.intelligence.contract import number
from .analysis import reverse_dcf


def trailing_metric(rows,metric):
    facts=[r for r in rows if r['kind']=='fundamental' and r['payload']['metric']==metric and r['temporal']['state']!='excluded']
    active=[r for r in facts if r['temporal']['state']=='current' and r.get('period_start')]
    if not active:return None
    latest_end=max(r['period_end'] for r in active)
    completed_year=[r for r in active if r['period_end']==latest_end and r['payload']['duration_class']=='annual']
    if completed_year:
        year=max(completed_year,key=lambda r:r.get('published_at') or '')
        return {'value':year['payload']['value'],'end':year['period_end'],'ids':[year['id']],'method':'reported fiscal year'}
    latest=max(active,key=lambda r:(r['period_end'],-(datetime.fromisoformat(r['period_end'])-datetime.fromisoformat(r['period_start'])).days))
    if latest['payload']['duration_class']=='annual':
        return {'value':latest['payload']['value'],'end':latest['period_end'],'ids':[latest['id']],'method':'reported fiscal year'}
    # Prefer latest fiscal YTD; one reported quarter can serve as Q1 YTD only when adjacent to FY end.
    end=latest['period_end']
    ytds=[r for r in active if r['period_end']==end and r['payload']['duration_class'] in {'quarter','ytd'}]
    for ytd in sorted(ytds,key=lambda r:r['period_start']):
        start=datetime.fromisoformat(ytd['period_start']);finish=datetime.fromisoformat(end)
        annuals=[r for r in facts if r['payload']['duration_class']=='annual' and 0<=(start-datetime.fromisoformat(r['period_end'])).days<=7 and r['payload']['unit']==ytd['payload']['unit']]
        if not annuals:continue
        annual=max(annuals,key=lambda r:r.get('published_at') or '')
        priors=[]
        for r in facts:
            if not r.get('period_start') or r['payload']['unit']!=ytd['payload']['unit']:continue
            a=datetime.fromisoformat(r['period_start']);b=datetime.fromisoformat(r['period_end'])
            if 350<=(finish-b).days<=380 and abs((finish-start).days-(b-a).days)<=12 and abs((a-datetime.fromisoformat(annual['period_start'])).days)<=7:priors.append(r)
        if not priors:continue
        prior=max(priors,key=lambda r:r.get('published_at') or '')
        return {'value':annual['payload']['value']+ytd['payload']['value']-prior['payload']['value'],
                'end':end,'ids':[annual['id'],ytd['id'],prior['id']],'method':'last fiscal year + current YTD - matching prior YTD'}
    return None


def valuation(rows,ticker):
    own=[r for r in rows if r['ticker']==ticker];out={};provenance=[]
    profiles=[r['payload'] for r in own if r['kind']=='profile']
    description=' '.join(str(p.get('industry',''))+' '+str(p.get('sic_description','')) for p in profiles).lower()
    specialized=any(word in description for word in ('bank','reit','insurance','real estate investment'))
    trailing={m:trailing_metric(own,m) for m in ('revenue','net_income','cfo','capex','sbc')}
    out['trailing']=trailing
    cfo,capex=trailing['cfo'],trailing['capex']
    if not specialized and cfo and capex and cfo['end']==capex['end']:
        fcf=cfo['value']-capex['value'];out['ttm_fcf_proxy']=fcf;provenance+=cfo['ids']+capex['ids']
        sbc=trailing['sbc']
        if sbc and sbc['end']==cfo['end']:out['ttm_fcf_less_sbc']=fcf-sbc['value']
    else:fcf=None
    quotes=[r for r in own if r['kind']=='price' and r['temporal']['state']=='current' and number(r['payload'].get('price')) and r['payload'].get('currency')=='USD']
    shares=[r for r in own if r['kind']=='fundamental' and r['payload']['metric']=='shares_outstanding' and r['temporal']['state']=='current' and r['payload']['unit']=='shares']
    if specialized:
        out['sector_method']='Use sector-specific capital/FFO/insurance valuation; generic CFO-minus-capex reverse DCF is not applied.'
    if quotes and shares:
        q=max(quotes,key=lambda r:r['observed_at']);sh=max(shares,key=lambda r:r['period_end'])
        from .temporal import iso
        split_dates=[iso(p['lastSplitDate'])[:10] for p in profiles if p.get('lastSplitDate')]
        if split_dates and max(split_dates)>sh['period_end']:
            out['equity_value_blocker']='Stock split follows the reported share count; reconcile shares before valuation.'
            return out
        cap=q['payload']['price']*sh['payload']['value'];out['equity_value_proxy']=cap
        out['share_count_date']=sh['period_end'];out['price_date']=q['observed_at'];provenance += [q['id'],sh['id']]
        out['equity_value_inputs']={'reference_price':q['payload']['price'],'currency':'USD','reported_shares_outstanding':sh['payload']['value'],'share_basis':'point-in-time shares outstanding, not quarterly weighted diluted shares'}
        if sh['payload'].get('share_classes'):out['equity_value_inputs'].update(share_classes=sh['payload']['share_classes'],share_basis=sh['payload'].get('aggregation'),price_basis='Selected listing price applied to reported common classes; differing class prices are not reconciled')
        out['equity_value_basis']='Current reference price × reported dated shares; not live market capitalization; corporate actions since the share date require reconciliation'
        if number(fcf) and fcf>0:
            out['fcf_yield_pct']=fcf/cap*100
            out['implied_growth_sensitivity']=[reverse_dcf(cap,fcf,discount=d) for d in (.08,.10,.12)]
            revenue=trailing['revenue']
            if revenue and revenue['end']==cfo['end'] and 0<fcf<revenue['value']:
                hurdle=out['implied_growth_sensitivity'][1]
                growth=hurdle.get('required_growth_pct')
                if growth is not None:
                    cash_margin=fcf/revenue['value'];years=hurdle['years']
                    required_cash=fcf*(1+growth/100)**years
                    out['operating_bridge']={'period_end':revenue['end'],'years':years,
                        'discount_rate':hurdle['discount_rate'],'terminal_growth':hurdle['terminal_growth'],
                        'required_year_end_fcf':required_cash,'current_fcf_margin_pct':cash_margin*100,
                        'basis':'Exploratory cash-margin assumptions: current trailing margin, plus five and ten percentage points. Not management guidance or forecasts. End-year revenue requirements do not prove the intervening annual cash-flow path.',
                        'scenarios':[{'assumed_fcf_margin_pct':margin*100,'required_year_end_revenue':required_cash/margin,
                            'required_revenue_cagr_pct':((required_cash/margin/revenue['value'])**(1/years)-1)*100}
                            for margin in (cash_margin,cash_margin+.05,cash_margin+.10) if margin<1]}
                    provenance+=revenue['ids']
        income=trailing['net_income'];revenue=trailing['revenue']
        if income and income['value']>0:
            out['earnings_multiple_proxy']=cap/income['value'];provenance+=income['ids']
        if revenue and revenue['value']>0:
            out['sales_multiple_proxy']=cap/revenue['value'];provenance+=revenue['ids']
    out['evidence_ids']=list(dict.fromkeys(provenance))
    out['interpretation']='Reverse expectations sensitivity, not a price target. CFO minus reported investment payments is an equity FCF proxy; reconcile leases, financing, SBC, acquisitions and tag definitions.'
    return out
