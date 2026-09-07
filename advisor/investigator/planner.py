"""Decision-changing questions and sector-specific investigation routes."""
SECTOR_LENSES={
 'semiconductors': {'match':['semiconductor'], 'questions':[
  'Separate customer capex announcements from funded GPU purchases, deployment and monetization.',
  'Compare supplier advanced-packaging/HBM capacity, lead times and customer inventory against shipment claims.',
  'Test export-license exposure, geographic mix, custom silicon competition, product transitions and gross-margin economics.',
  'Reconcile receivables, customer financing, strategic investments and revenue concentration; do not double count ecosystem funding.'],
  'sources':['customers','suppliers','peers','industry_associations','bis_trade']},
 'software': {'match':['software','information technology services'], 'questions':[
  'Compare ARR, net retention, remaining obligations, bookings and recognized revenue on consistent definitions.',
  'Separate AI seat/usage monetization from displaced seats, inference cost, capex and customer optimization.',
  'Measure SBC-adjusted cash generation, dilution, churn and deferred-revenue changes.'],
  'sources':['issuer_ir','transcripts','customers','peers','sec_quality']},
 'banks': {'match':['bank','credit services'], 'questions':[
  'Analyze deposits, funding costs, NIM, securities marks, capital ratios and liquidity together.',
  'Compare delinquency migration, charge-offs, reserve coverage and underwriting vintages.',
  'Use bank-specific valuation and capital distributions; generic corporate FCF is inappropriate.'],
  'sources':['sec_quality','fed','credit','issuer_ir']},
 'biopharma': {'match':['biotech','drug','pharma','medical'], 'questions':[
  'Verify registered primary endpoints, statistical hierarchy, control arm, safety and population.',
  'Separate trial readout, FDA submission, acceptance, advisory meeting and approval; date each catalyst.',
  'Measure cash runway through the next readout, financing dilution, addressable population and reimbursement.'],
  'sources':['clinical_trials','fda','sec_dilution','issuer_ir','peers']},
 'energy': {'match':['oil','gas','energy','coal'], 'questions':[
  'Separate commodity-price exposure, hedge book, production volume, decline rates and operating cost.',
  'Compare inventory releases with seasonal demand, OPEC supply and marginal economics.',
  'Test maintenance capex, reserve replacement, leverage and distribution coverage under lower commodity prices.'],
  'sources':['eia','sec_quality','transcripts','credit']},
 'consumer': {'match':['retail','apparel','restaurants','consumer','travel','leisure'], 'questions':[
  'Separate traffic, ticket, price, mix and units; compare promotions, inventory and gross margins.',
  'Test whether weak demand is company-specific or shared by competitors and customer cohorts.',
  'Compare credit/consumer pressure, store productivity, channel checks and inventory markdown risk.'],
  'sources':['bls_bea','peers','alternative','transcripts','sec_quality']},
 'industrials': {'match':['industrial','aerospace','defense','machinery','electrical'], 'questions':[
  'Separate funded backlog, options, contract ceilings, cancellations and revenue conversion.',
  'Compare book-to-bill, capacity, working capital, input costs and fixed-price contract exposure.',
  'Trace customer capex and agency obligations rather than relying on award headlines.'],
  'sources':['usaspending','customers','suppliers','sec_quality']},
 'real_estate': {'match':['reit','real estate'], 'questions':[
  'Use FFO/AFFO reconciliations, same-property NOI, occupancy, lease roll and debt maturities.',
  'Stress refinancing rates and asset cap rates; separate maintenance spending from development.',
  'Test dividend coverage and tenant credit; generic EPS/FCF comparisons can mislead.'],
  'sources':['sec_quality','credit','fred','transcripts']},
}

HYPOTHESIS_REQUIREMENTS={
 'quality_compounder':['fundamentals','accounting','valuation','expectations'],
 'expectations_reset':['expectations','guidance','valuation','technicals'],
 'oversold_recovery':['technicals','fundamentals','catalysts'],
 'trend_continuation':['technicals','expectations','valuation'],
 'short_squeeze':['positioning','catalysts','technicals','options'],
 'crowded_long':['sentiment','ownership','expectations','valuation'],
 'neglected_turnaround':['sentiment','fundamentals','guidance'],
 'lottery_speculation':['options','catalysts','financing','technicals'],
 'accounting_deterioration':['accounting','fundamentals','guidance'],
 'financing_stress':['financing','accounting','catalysts'],
 'second_order':['industry','fundamentals','expectations'],
 'catalyst_repricing':['catalysts','expectations','valuation','regulatory'],
}

def plan(rows,analysis,ticker):
    descriptions=' '.join(str(r['payload'].get('industry',''))+' '+str(r['payload'].get('sector',''))+' '+str(r['payload'].get('sic_description','')) for r in rows if r['ticker']==ticker and r['kind']=='profile').lower()
    lenses=[dict(name=name,**v) for name,v in SECTOR_LENSES.items() if any(x in descriptions for x in v['match'])]
    dimensions={c['dimension']:c['status'] for c in analysis['coverage']}
    questions=[]
    for h in analysis['hypotheses']:
        missing=[d for d in HYPOTHESIS_REQUIREMENTS[h['name']] if dimensions.get(d)!='current_inputs']
        conflict=bool(h['support'] and h['challenge'])
        priority='high' if conflict or h['attention_priority']==3 else 'medium' if h['attention_priority'] else 'coverage'
        questions.append({**h,'missing_dimensions':missing,'priority':priority,
                          'reason':'Resolve opposing evidence before choosing a direction' if conflict else 'Test a potentially material mechanism' if h['attention_priority'] else 'Check applicability; do not force a thesis',
                          'stop_when':'Mechanism confirmed or falsified with dated evidence; otherwise report the exact unresolved premise'})
    questions.sort(key=lambda q:({'high':0,'medium':1,'coverage':2}[q['priority']],len(q['missing_dimensions'])))
    return {'sector_lenses':lenses,'research_queue':questions,
            'decision_test':'Would resolving this question change the action, timing, risk or thesis? Prioritize that over collecting another similar headline.',
            'ranking':'Ordinal research priority based on material consequences, contradictions and missing premises; not an estimated return score.'}
