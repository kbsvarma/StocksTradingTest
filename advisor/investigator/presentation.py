"""Evidence-linked decision briefs, with explicit gates and no invented forecasts."""
from difflib import get_close_matches
from .collectors import symbol
from .temporal import annotate, utcnow

ALIASES={'NVIDIA':'NVDA','MICROSOFT':'MSFT','APPLE':'AAPL','AMAZON':'AMZN','ALPHABET':'GOOGL','GOOGLE':'GOOGL','TESLA':'TSLA','META':'META','PALANTIR':'PLTR'}

def resolve_query(query, data=None):
    from .search import resolve
    return resolve(query,data)


def suggestions(ticker, symbols=()):
    if ticker=='NVDIA':return ['NVDA']
    return get_close_matches(ticker,sorted(set(symbols)|set(ALIASES.values())),n=3,cutoff=.7)


MEANING={
 'revenue_growth':('Demand is expanding, but the share price responds to growth versus expectations.','Compare the next outlook with period-matched consensus; growth alone is not a buy trigger.'),
 'profit_growth':('Profit growth can strengthen operating leverage, provided it comes from recurring operations.','Reconcile one-off gains, taxes and share-count changes before extrapolating earnings.'),
 'receivables_divergence':('Sales are converting into receivables faster than cash. Collection timing or customer terms could weaken earnings quality.','Check days sales outstanding, payment terms and collections in the next filing. This flag is not proof of misconduct.'),
 'inventory_divergence':('Inventory is building faster than sales, creating potential markdown or product-transition risk.','Verify inventory mix, purchase commitments and subsequent sell-through.'),
 'cash_conversion':('Reported earnings have weaker cash backing over the matched period. That reduces confidence in a growth-only thesis.','Reconcile working capital and tax timing; watch whether cash conversion recovers in the next comparable period.'),
 'estimate_revision':('Analysts are changing the earnings path the market is pricing. This is more forward-looking than last quarter’s growth.','Check whether revisions continue after the next outlook and whether price has already moved further than estimates.'),
 'growth_revision_conflict':('Strong historical growth and falling future estimates point in opposite directions. A good quarter can still precede a weaker outlook.','Resolve the outlook and margin bridge before treating historical growth as an entry signal.'),
 'long_trend':('The long-term price trend shows market acceptance or rejection; it does not establish fair value.','Use the dated moving average as a trend checkpoint, alongside earnings and valuation.'),
 'relative_strength':('Performance versus SPY separates stock-specific momentum from a broad market move.','Require persistent relative strength and an identifiable business driver.'),
 'rsi_extreme':('An extreme momentum reading signals stretch, not an automatic reversal.','Wait for stabilization or continuation confirmation rather than trading the oscillator alone.'),
 'short_crowding':('A large short position can amplify a move, but may also reflect well-founded business concerns.','A squeeze thesis needs dated borrow pressure, a positive catalyst and price/volume confirmation together.'),
 'lottery_risk':('Large gaps or extreme volatility can dominate the business thesis and overwhelm a normal stop.','Identify the binary event and financing runway before considering an event-driven position.'),
 'illiquidity':('Limited trading capacity can make a quoted entry or stop unrealistic.','Check spreads and executable liquidity before sizing any position.'),
 'dilution':('A growing share count can offset business growth for each existing share.','Reconcile splits and issuance and evaluate growth on a per-share basis.'),
}


def decision_brief(report, now=None):
    """Revalidate source clocks at viewing time; do not mutate immutable snapshots."""
    now=now or utcnow();ticker=report['ticker'];analysis=report['analysis']
    rows=annotate(report.get('evidence',[]),now);lookup={r['id']:r for r in rows}
    own=[r for r in rows if r['ticker']==ticker]
    identified=any(r['kind'] in {'fundamental','price','technical'} or (r['kind']=='profile' and any(r['payload'].get(k) for k in ('name','shortName','longName','cik','sic_description'))) for r in own)
    findings=[];historical=[]
    for item in analysis.get('findings',[]):
        item=dict(item)
        if item['id']=='estimate_revision':
            periods={'0q':'Current quarter','+1q':'Next quarter','0y':'Current fiscal year','+1y':'Next fiscal year'}
            item['detail']='; '.join(f'{periods.get(k.split("_")[0],k)}: EPS estimate {v:+.1f}% over 30 days' for k,v in analysis.get('estimates',{}).items() if k.endswith('eps_revision_30d_pct'))+'. Current provider fiscal-period labels; not pre-event consensus.'
        if item['id']=='cash_conversion':
            fund=analysis.get('fundamentals',{});window=fund.get('cashflow_window',{});ratio=fund.get('cash_conversion')
            if ratio is not None:item['detail']=f'Operating cash flow was {ratio:.2f} times net income over {window.get("start")} to {window.get("end")}. Both amounts use the same reporting window.'
        sources=[lookup[x] for x in item['evidence_ids'] if x in lookup]
        # Historical comparisons are expected; at least one current anchor and no excluded input are necessary.
        valid=bool(sources) and len(sources)==len(item['evidence_ids']) and any(r['temporal']['state']=='current' for r in sources) and all(r['temporal']['state']!='excluded' for r in sources)
        (findings if valid else historical).append(item)
    bull=[f for f in findings if f['direction']=='bullish'];bear=[f for f in findings if f['direction']=='bearish']
    ids={f['id'] for f in findings};drivers=[]
    if 'revenue_growth' in ids and {'cash_conversion','receivables_divergence','inventory_divergence'} & ids:
        drivers.append('Growth and earnings quality diverge. Strong sales do not resolve the cash or working-capital concerns; those concerns need to be explained before upgrading the thesis.')
    if 'estimate_revision' in ids and 'long_trend' in ids:
        rev=next(f for f in findings if f['id']=='estimate_revision');trend=next(f for f in findings if f['id']=='long_trend')
        drivers.append('Earnings revisions and the long-term trend agree, which supports investigating continuation. Valuation and the next catalyst still decide whether the setup is attractive.' if rev['direction']==trend['direction']=='bullish' else 'Compare the forward earnings path with price confirmation: disagreement is a reason to wait for the business outlook or trend to resolve.')
    if 'lottery_risk' in ids:drivers.append('Gap risk is material. Treat this as a potential event-driven speculation, not a conventional trend setup until the catalyst and downside are understood.')
    if not identified:
        verdict='SYMBOL NOT VERIFIED';tone='red';summary=f'We could not verify {ticker} as the requested security. Macro observations and benchmark prices cannot establish a company thesis.'
    elif not findings:
        verdict='NO USABLE CONCLUSION';tone='amber';summary=f'{ticker} does not have enough current, company-specific evidence for a directional assessment. This is a coverage failure, not a neutral investment opinion.'
    elif bull and bear:
        verdict='MIXED · WAIT FOR CONFIRMATION';tone='amber';summary=f'{bull[0]["title"]} supports the upside case, while {bear[0]["title"].lower()} challenges it. The evidence does not yet justify a clean directional call.'
    elif bear:
        verdict='DOWNSIDE CONCERNS · REVIEW';tone='red';summary=f'The leading concern is {bear[0]["title"].lower()}. This warrants a downside review; it does not by itself establish a short entry.'
    elif bull:
        verdict='CONSTRUCTIVE · CONDITIONAL WATCH';tone='green';summary=f'The strongest support is {bull[0]["title"].lower()}. Watch for confirmation; valuation and event risk must support an entry.'
    else:
        verdict='WATCH · NO DIRECTIONAL EDGE';tone='cyan';summary='The current observations identify questions to investigate, but do not establish a directional advantage.'
    tech=analysis.get('technicals',{}) if any(r['kind']=='technical' and r['temporal']['state']=='current' for r in own) else {}
    conditions=[]
    if findings and tech:
        high,low,ma=tech.get('prior_20_high'),tech.get('prior_20_low'),tech.get('ma200')
        if high and low:
            conditions.append(('Upside confirmation',f'A daily close above {high:,.2f}, the preceding 20-session high, with stronger volume and supportive earnings evidence. A price breakout alone is insufficient.'))
            conditions.append(('Downside checkpoint',f'A daily close below {low:,.2f}, the preceding 20-session low, weakens the recovery/continuation case. This is a scenario threshold, not an executable stop.'))
        if ma:conditions.append(('Long-term trend',f'The 200-session average is {ma:,.2f}; reassess trend support if price loses it. Refresh the investigation before using these historical levels.'))
    synthesis=report.get('synthesis',{})
    reviewed=[];basis='Evidence-based rules assessment · No model review'
    if synthesis.get('review_status')=='model_challenged':
        from .reasoner import validate_synthesis
        reviewed,rejected=validate_synthesis(synthesis,rows)
        if rejected or not reviewed:
            reviewed=[]
            basis='Prior synthesis needs fresh evidence · Current rules assessment shown'
        else:
            action=synthesis.get('action','investigate_further')
            verdict,tone={
                'buy_candidate':('BUY CANDIDATE · CONDITIONAL','green'),
                'wait_for_trigger':('WAIT FOR TRIGGER','amber'),
                'avoid_new_entry':('AVOID NEW ENTRY','red'),
                'reduce_candidate':('REDUCE CANDIDATE · REVIEW','red'),
                'no_edge_found':('NO EDGE FOUND','cyan'),
            }.get(action,('FURTHER INVESTIGATION','amber'))
            summary=synthesis.get('summary') or synthesis.get('action_reason') or summary
            drivers=[synthesis.get('action_reason',''),*synthesis.get('contradictions',[])]
            drivers=[x for x in drivers if x]
            conditions=[('Entry / confirmation',x) for x in synthesis.get('entry_conditions',[])]+[('Exit / invalidation',x) for x in synthesis.get('exit_conditions',[])]+[('Next check',x) for x in synthesis.get('next_checks',[])]
            basis='Source-checked synthesis · Adversarial model review'
    if synthesis.get('review_status')=='requires_more_evidence':
        from .reasoner import validate_synthesis
        reviewed,_=validate_synthesis(synthesis,rows)
        if reviewed:
            verdict,tone='RESEARCH COMPLETE · ACTION NEEDS REVIEW','amber'
            summary=synthesis.get('action_reason') or 'Reviewed insights are available below; no directional action passed review.'
            conditions=[('Next check',x) for x in synthesis.get('next_checks',[])]
            basis='Source-checked insights · Proposed action did not pass review'
    direct=synthesis.get('review_status')=='source_linked_brief' and bool(synthesis.get('source_ids')) and all(lookup.get(i,{}).get('temporal',{}).get('state') in {'current','context_only'} for i in synthesis.get('source_ids',[])) and any(lookup.get(i,{}).get('temporal',{}).get('state')=='current' for i in synthesis.get('source_ids',[]))
    if direct:
        verdict,tone={'buy_candidate':('BUY CANDIDATE','green'),'hold':('HOLD','cyan'),'avoid_new_entry':('AVOID NEW ENTRY','red'),'no_edge_found':('NO EDGE FOUND','amber')}.get(synthesis.get('action'),('RESEARCH BRIEF','cyan'))
        coverage=synthesis.get('research_coverage',{})
        if synthesis.get('action')=='no_edge_found' and coverage.get('valuation_calculation') is False:
            verdict,tone='VALUATION UNRESOLVED · RESEARCH GAP','amber'
        if synthesis.get('verification_state')=='unavailable':
            verdict,tone='DRAFT · '+verdict,'amber'
        summary=synthesis['summary'];drivers=[];conditions=[]
        basis=synthesis.get('validation_basis','Original-source research brief · No separate adversarial model review')
    if identified and not reviewed and not direct:
        errors=' '.join(str(x) for x in report.get('errors',[]))
        conditions=[]
        if 'quota or rate limit' in errors:
            verdict='ANALYSIS BLOCKED · MODEL LIMIT';tone='red'
            summary=f'{ticker} source collection completed, but Gemini refused the analysis because its quota or rate limit was reached. No investment conclusion was produced.'
        elif synthesis.get('review_status')=='model_challenged':
            verdict='RESEARCH OUTDATED · REFRESH NEEDED';tone='amber'
            summary=f'{ticker} previously had a reviewed report, but its cited evidence no longer passes current-date checks. The findings below are supporting data, not a refreshed call.'
        elif report.get('mode')=='deep':
            verdict='ANALYSIS INCOMPLETE · RETRY NEEDED';tone='amber'
            summary=f'{ticker} has collected evidence, but no synthesis passed the source and review checks. '+(errors[-400:] if errors else 'The investigation needs to finish before a call can be shown.')
        else:
            verdict='NOT INVESTIGATED · DATA SCAN ONLY';tone='cyan'
            summary=f'{ticker} has calculated signals, but its full research and model review have not run. These observations are not an investment recommendation.'
        basis='No current reviewed investment conclusion'
    gaps=[]
    for name,r in report.get('collection',{}).items():
        if r.get('status') in {'failed','partial'}:
            issues='; '.join(r.get('errors',[])) or 'Some requests did not return evidence'
            gaps.append((name.replace('_',' ').title(),issues))
    if not findings:gaps.insert(0,('Company evidence','No current findings from the company’s fundamentals, market data or earnings expectations. Verify the ticker and rerun.'))
    return dict(insights=reviewed,basis=basis,verdict=verdict,tone=tone,summary=summary,drivers=drivers,findings=findings,historical=historical,bull=bull,bear=bear,conditions=conditions,gaps=gaps,identified=identified,technicals=tech,lookup=lookup)
