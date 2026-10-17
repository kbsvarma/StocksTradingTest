"""Financial relationships the research model must explain, not invent.

This is an analytical memo, not an automated trading recommendation. Every
conclusion is conditional on comparable, dated inputs from the financial engine.
"""
from advisor.intelligence.contract import number


def reconcile(analysis):
    f=analysis.get('fundamentals',{});v=analysis.get('valuation',{})
    citations=analysis.get('fundamental_evidence',{});items=[]
    def add(key,title,observation,consequence,question,metrics,period):
        ids=list(dict.fromkeys(i for m in metrics for i in citations.get(m,[])))
        if key=='valuation':ids=list(dict.fromkeys(ids+v.get('evidence_ids',[])))
        if not ids:return
        items.append(dict(id=key,title=title,observation=observation,consequence=consequence,
                          question=question,evidence_ids=ids,period=period))
    period=f.get('revenue_period')
    gross=f.get('gross_profit_margin_change_pp');operating=f.get('op_income_margin_change_pp')
    net=f.get('net_income_margin_change_pp');below=f.get('below_operating_margin_change_pp')
    if all(number(x) for x in (operating,net,below)):
        consequence=('Most of the net-margin improvement arose below operating profit. '
                     'Headline earnings growth therefore overstates the improvement in operating profitability.'
                     if net>0 and below>net/2 else
                     'Operating performance and below-operating items must be distinguished before extrapolating net earnings.')
        amount=f.get('net_income_amount_change');op_amount=f.get('op_income_amount_change')
        if all(number(x) for x in (amount,op_amount)) and f.get('net_income_amount_unit')==f.get('op_income_amount_unit')=='USD':
            consequence+=(f' In dollar terms, net income changed {amount/1e9:+.3f}bn USD and operating income '
                          f'{op_amount/1e9:+.3f}bn; the below-operating dollar residual is {(amount-op_amount)/1e9:+.3f}bn.')
            if amount>0:
                consequence+=f' That residual accounts for {(amount-op_amount)/amount*100:.1f}% of the dollar increase in net income. The share of net-margin expansion is a different calculation.'
        add('earnings','Separate operating improvement from headline profit',
            f'Quarter ended {period}: '+(f'gross margin {gross:+.2f}pp; ' if number(gross) else 'gross-margin comparison unavailable; ')+f'operating margin {operating:+.2f}pp; '
            f'net margin {net:+.2f}pp. The below-operating residual is {below:+.2f}pp.',consequence,
            'Reconcile taxes, investment gains, interest and other income in the latest release and filing. '
            'Quantify each disclosed contribution; do not attribute the entire residual to one item.',
            ['earnings_bridge'],period)
    window=f.get('cashflow_window',{})
    cfo=f.get('cashflow_cfo');capex=f.get('cashflow_capex');fcf=f.get('free_cash_flow')
    prior=f.get('prior_free_cash_flow');cfo_growth=f.get('cashflow_cfo_yoy_pct')
    if not analysis.get('metric_applicability',{}).get('cash_flow_valuation') and all(number(x) for x in (cfo,capex,fcf)):
        unit=f.get('cashflow_unit','reported units')
        obs=f'{window.get("start")} to {window.get("end")}: operating cash flow {cfo/1e9:.3f}bn {unit}, capital expenditure {capex/1e9:.3f}bn, leaving {fcf/1e9:.3f}bn.'
        consequence='Cash generation after investment is positive.' if fcf>0 else 'Reported investment exceeds operating cash generation.'
        if number(prior):
            obs+=f' Comparable prior-window free cash flow was {prior/1e9:.3f}bn; change {(fcf-prior)/1e9:+.3f}bn.'
            if fcf<prior and number(cfo_growth) and cfo_growth>0:
                consequence=('Operating cash generation grew, but incremental capital expenditure more than absorbed that growth. '
                             'This is a reinvestment burden, not evidence that operating cash generation collapsed. '
                             +('Free cash flow remains positive.' if fcf>0 else 'Free cash flow is now negative.'))
            elif fcf>prior:
                consequence+=' Cash available after reported capital expenditure improved versus the comparable window.'
        add('cash','Identify where the cash went',obs,consequence,
            'Does disclosed capacity demand and expected monetization justify the investment? '
            'Read capital expenditure guidance, capacity constraints, utilization and lease commitments; '
            'distinguish growth investment from maintenance before extrapolating cash flow.',
            ['cash_conversion','free_cash_flow_yoy_pct'],window.get('end'))
    for metric in ('receivables','inventory'):
        growth=f.get(metric+'_yoy_pct');revenue=f.get('revenue');rg=f.get('revenue_yoy_pct');balance=f.get(metric)
        if not all(number(x) for x in (growth,revenue,rg,balance)) or revenue<=0 or f.get(metric+'_period')!=period:continue
        scale=balance/revenue*100
        if growth<=rg or scale<5:continue
        add(metric,'Test '+metric+' against business scale',
            f'{metric.title()} grew {growth:.1f}% versus revenue {rg:.1f}%; balance {balance/1e9:.3f}bn, '
            f'equivalent to {scale:.1f}% of one quarter of revenue as of {period}.',
            'This identifies a material explanation to investigate, not a proven deterioration. '
            'A documented change in payment terms or a planned product build can be consistent with the increase.',
            'Check management’s explanation against aging, payment terms, subsequent collections or sell-through. '
            'A credible explanation and the balance increase are not themselves a contradiction.',
            [metric+'_yoy_pct','revenue_yoy_pct'],period)
    sensitivity=[s for s in v.get('implied_growth_sensitivity',[]) if number(s.get('required_growth_pct'))]
    if sensitivity and number(v.get('ttm_fcf_proxy')):
        middle=next((s for s in sensitivity if s['discount_rate']==.10),sensitivity[len(sensitivity)//2])
        obs=(f'Trailing free-cash-flow proxy {v["ttm_fcf_proxy"]/1e9:.3f}bn; dated equity-value proxy '
             f'{v["equity_value_proxy"]/1e12:.3f}tn; cash-flow yield {v["fcf_yield_pct"]:.2f}%. '
             f'At {middle["discount_rate"]:.0%} discount and {middle["terminal_growth"]:.0%} terminal growth, '
             f'the price requires {middle["required_growth_pct"]:.1f}% annual FCF growth for {middle["years"]} years.')
        required_cash=v['ttm_fcf_proxy']*(1+middle['required_growth_pct']/100)**middle['years']
        obs+=f' That path reaches {required_cash/1e9:.1f}bn of annual FCF in year {middle["years"]}; this is the scenario requirement, not a prediction.'
        add('valuation','Translate the price into a business requirement',obs,
            'This is a scenario hurdle, not a forecast or an observed market belief. '
            'A single historical quarter cannot validate a multi-year cash-flow path; revenue growth is not FCF growth.',
            'Build a source-supported revenue, margin and reinvestment path to that cash-flow hurdle. '
            'If current investment is temporarily depressing cash flow, quantify the recovery rather than labeling the stock expensive from trailing yield alone.',
            [],v.get('price_date'))
    specialized=analysis.get('metric_applicability',{}).get('cash_flow_valuation') or v.get('sector_method')
    if specialized:
        pe=v.get('earnings_multiple_proxy')
        add('valuation','Use the sector’s earnings and capital economics',
            (f'Reported trailing earnings multiple: {pe:.2f} times. ' if number(pe) else '')+specialized,
            'Reported earnings need normalization before a valuation conclusion. Generic corporate free-cash-flow signals do not establish bank distress or cheapness.',
            'Reconcile reported and adjusted earnings, tangible book value, sustainable return on tangible equity, credit losses and regulatory capital.',
            [],v.get('price_date'))
    lease=next((x for x in analysis.get('findings',[]) if x['id']=='lease_classification_change'),None)
    for bridge in analysis.get('non_gaap_reconciliations',[]):
        if bridge.get('rounded_headline'):
            items.insert(0,{'id':'non_gaap_'+bridge['scope'],'title':'Separate headline profit from company-adjusted earnings',
                'observation':f"Reported quarterly net income was {bridge['reported_net_income']/1e9:.3f}bn USD; management disclosed approximately {bridge['adjusted_net_income']/1e9:.1f}bn excluding {bridge['scope']}.",
                'consequence':f"Company-adjusted earnings are approximately {bridge['adjusted_share_of_reported_pct']:.0f}% of reported earnings. "+bridge['interpretation'] if bridge.get('adjusted_share_of_reported_pct') is not None else bridge['interpretation'],
                'question':'Reconcile the excluded items, taxes and sustainable returns on equity before using reported earnings multiples.',
                'evidence_ids':bridge['evidence_ids'],'period':bridge['period_end']})
            continue
        items.insert(0,{'id':'non_gaap_'+bridge['scope'],'title':'Reconcile reported and company-adjusted earnings',
            'observation':f"{bridge['scope']} adjustment: reported net income {bridge['reported_net_income']/1e9:.3f}bn USD plus adjustment {bridge['reconciliation_adjustment']/1e9:+.3f}bn equals company-adjusted income {bridge['adjusted_net_income']/1e9:.3f}bn.",
            'consequence':f"This item contributed {bridge['impact_on_reported_earnings']/1e9:+.3f}bn to current reported earnings versus {bridge['prior_impact_on_reported_earnings']/1e9:+.3f}bn in the comparable prior quarter: a {bridge['year_over_year_change_in_impact']/1e9:+.3f}bn year-over-year change. "+bridge['interpretation'],
            'question':'Identify any other discrete gains or expenses that remain after this source-defined adjustment before estimating recurring earnings.',
            'evidence_ids':bridge['evidence_ids'],'period':bridge['period_end']})
    if lease:
        items.insert(0,{'id':lease['id'],'title':lease['title'],'observation':'A dated issuer call describes a change from finance to operating leases.',
            'consequence':lease['detail'],'question':'Compare cash capex, finance-lease additions, operating-lease payments and total contractual investment on the same basis before identifying an investment slowdown.',
            'evidence_ids':lease['evidence_ids'],'period':lease['as_of']})
    return {'basis':'Reconciled financial evidence; business explanations remain explicit research questions',
            'operating_bridge':v.get('operating_bridge'),
            'items':items,'decision_standard':'An actionable valuation thesis requires a dated business scenario and a reason the current price fails to reflect it. A moving-average crossing or a small change from the last quarter is not sufficient.'}
