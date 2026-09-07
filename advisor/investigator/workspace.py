"""Readable investment briefs with source-linked reasoning and explicit coverage failures."""
import json
from html import escape
from pathlib import Path
import pandas as pd
import streamlit as st
from .engine import load_latest, load_run, markdown
from .jobs import start, read_status
from .presentation import decision_brief, MEANING
from .search import resolve, LABELS


def rich(text):st.markdown(text,unsafe_allow_html=True)
def safe(value):return escape(str(value))
def label(value):return str(value).replace('_',' ').capitalize()


def render(data,principal,selected=None):
    ticker=st.session_state.get('investigator_ticker') or selected
    if not ticker:
        st.info('Search for a company or ticker above to begin.');return
    # Older saved workspaces can contain a misspelling; resolve known names without a provider request.
    from .search import NAMES
    ticker=NAMES.get(str(ticker).upper(),str(ticker).upper())
    a,b=st.columns([5,1],vertical_alignment='center')
    with a:rich(f'<div class="ad-kicker">ON-DEMAND RESEARCH</div><div class="ad-section-title">{safe(LABELS.get(ticker,ticker))} <span class="ad-security-symbol">{safe(ticker)}</span></div>')
    with b:
        requested=st.button('Run fresh investigation',type='primary',use_container_width=True,disabled=principal.role not in {'analyst','admin'})
    pending=st.session_state.pop('ad_investigate_requested',None)
    if requested or pending==ticker:
        try:
            verified=resolve(ticker,data)
            job=start(data,verified,principal,deep=True)
            st.session_state['investigation_job']=job
            st.toast('Investigating evidence, expectations and counterarguments')
        except (ValueError,RuntimeError,PermissionError) as exc:st.error(str(exc))
    from .runtime import status as runtime_status
    runtime=runtime_status()
    if runtime['configured']:
        st.caption(f"Research engine: {runtime['provider']} · {runtime['model']}")
    if not runtime['configured']:
        st.warning('Research engine setup required: the application model credential is missing. Saved reports and charts remain available; a new full investigation cannot run yet.')
    from advisor.market_view import render_market
    render_market(data,ticker)
    job=st.session_state.get('investigation_job')
    if not job or job.get('ticker')!=ticker:
        try:job=json.loads((Path(data)/'intelligence/investigations'/ticker/'active.json').read_text())
        except (ValueError,OSError):job=None
    if job and job.get('ticker')==ticker:
        @st.fragment(run_every='5s')
        def status_fragment():
            status=read_status(data,ticker,job['run_id'])
            if status.get('state') in {'queued','running'}:st.info(status.get('detail','Investigating'))
            elif status.get('state')=='failed':st.error(status.get('detail','Investigation failed'))
            elif st.session_state.get('ad_visible_run')!=job['run_id']:
                st.session_state['ad_visible_run']=job['run_id'];st.rerun()
        status_fragment()
    try:report=load_latest(data,ticker)
    except FileNotFoundError:
        st.info(f'Run a fresh investigation to examine {ticker} fundamentals, expectations, price structure and dated sources.');return
    except (ValueError,KeyError) as exc:st.error(str(exc));return
    brief=decision_brief(report);lookup=brief['lookup'];analysis=report['analysis'];s=report['synthesis']
    rich(f'<div class="ad-verdict {brief["tone"]}"><div class="ad-kicker">{safe(brief["verdict"])}</div><p>{safe(brief["summary"])}</p><small>AS OF {safe(report["as_of"][:19].replace("T"," "))} UTC · {safe(brief["basis"])}</small></div>')
    overview,evidence,sources=st.tabs(['DECISION BRIEF','EVIDENCE & DATES','SOURCES & GAPS'])
    with overview:
        if brief['drivers']:
            rich('<div class="ad-panel-head">WHAT MATTERS TO THE DECISION</div>')
            for line in brief['drivers']:st.write(line)
        tech=brief['technicals']
        if tech:
            cells=[]
            for key,title,suffix in [('close','Reference price',''),('rsi14','RSI · 14 sessions',''),('distance_ma200_pct','From 200-day average','%'),('relative_21d_pp','21-day vs S&P 500','pp')]:
                v=tech.get(key)
                if v is not None:cells.append(f'<div class="ad-reading"><small>{title}</small><strong>{v:,.2f}{suffix}</strong></div>')
            rich('<div class="ad-readings">'+''.join(cells)+'</div>')
            dates=sorted({r.get('observed_at') or '' for r in lookup.values() if r['ticker']==ticker and r['kind']=='technical'})
            st.caption('Price analysis uses adjusted daily bars through '+(dates[-1][:10] if dates else 'unknown')+'. Reference levels are not live quotes.')
        if brief['findings']:
            rich('<div class="ad-panel-head">THE INVESTMENT CASE <small>SUPPORT · CHALLENGE · CONSEQUENCE</small></div>')
            columns=st.columns(2)
            for col,title,items,tone in [(columns[0],'UPSIDE SUPPORT',brief['bull'],'green'),(columns[1],'DOWNSIDE / CONTRADICTIONS',brief['bear'],'red')]:
                with col:
                    rich(f'<div class="ad-kicker {tone}">{title}</div>')
                    if not items:st.caption('No current finding establishes this side of the case.')
                    for f in items:
                        implication,check=MEANING.get(f['id'],('This observation may change the thesis; establish its economic impact before acting.','Read the source and verify the terms and timing.'))
                        rich(f'<article class="ad-finding {tone}"><h3>{safe(f["title"])}</h3><p>{safe(f["detail"])}</p><div class="ad-meaning"><b>WHY IT MATTERS</b><p>{safe(implication)}</p></div><p class="ad-next"><b>Next test:</b> {safe(check)}</p></article>')
                        with st.expander('Sources · '+f['title']):
                            for eid in dict.fromkeys(f['evidence_ids']):
                                r=lookup.get(eid)
                                if not r:continue
                                st.markdown(f'**{r["title"] or label(r["source"])}**')
                                st.caption(f'Published {(r.get("published_at") or "unknown")[:10]} · measured {r.get("period_end") or r.get("observed_at") or "unknown"} · {r["temporal"]["state"]}')
                                if r.get('url'):st.link_button('Read original source',r['url'])
            neutral=[f for f in brief['findings'] if f['direction'] not in {'bullish','bearish'}]
            for f in neutral:
                st.markdown('**'+f['title']+'**');st.write(f['detail']);st.caption(MEANING.get(f['id'],('', ''))[1])
        if brief['insights']:
            st.subheader('Reviewed research insights')
            for i in brief['insights']:
                with st.container(border=True):
                    st.markdown('#### '+i['title'])
                    for name,key in [('What changed','what_changed'),('Why it matters','mechanism'),('Priced-in expectations','what_is_priced_in'),('Counterargument','counterargument'),('Invalidation','invalidation'),('Horizon','horizon')]:st.markdown(f'**{name}:** {i[key]}')
                    for citation in i.get('evidence',[]):
                        source=lookup.get(citation['source_id'])
                        if source:
                            st.caption(f"{source['title']} · published {source.get('published_at')} · period {source.get('period_end') or source.get('observed_at')}")
                            st.link_button('Original evidence',source['url'])
        value=analysis.get('valuation',{})
        with st.expander('Valuation · what the price requires',expanded=bool(value.get('fcf_yield_pct'))):
            if value.get('fcf_yield_pct') is not None:
                st.write(f"The dated equity-value proxy implies a **{value['fcf_yield_pct']:.2f}% trailing free-cash-flow yield**.")
                scenarios=value.get('implied_growth_sensitivity',[])
                for scenario in scenarios:
                    growth=scenario.get('required_growth_pct')
                    if growth is not None:st.write(f"At a {scenario['discount_rate']:.0%} required return, the model requires **{growth:.1f}% annual FCF growth** over {scenario['years']} years.")
                st.write('The decision test is whether the business can sustain that cash-growth path. Compare it with forward demand, margins and reinvestment needs before concluding the stock is cheap.')
                st.caption(f"Price reference: {value.get('price_date')} · reported shares: {value.get('share_count_date')}. CFO minus capex is a cash-flow proxy. Financing changes and SBC require reconciliation; these sensitivities are not price targets.")
            else:st.write(value.get('sector_method') or value.get('equity_value_blocker') or 'A period-matched cash-flow and equity-value comparison is unavailable. The report cannot establish whether the current price is attractive.')
        news=[r for r in lookup.values() if r['ticker']==ticker and r['kind'] in {'document','news'} and r['temporal']['state']=='current']
        if news:
            with st.expander('Recent releases & developments to verify'):
                st.caption('Dated source leads. Headline sentiment is not treated as a verified catalyst or an investment conclusion.')
                for r in sorted(news,key=lambda r:r.get('published_at') or '',reverse=True)[:8]:
                    st.markdown('**'+r['title']+'**');st.caption((r.get('published_at') or 'Publication date unavailable')+' · '+label(r['source']))
                    if r.get('url'):st.link_button('Open development',r.get('payload',{}).get('article_url') or r['url'])
        if brief['conditions']:
            rich('<div class="ad-panel-head">WHAT WOULD CHANGE THE CALL <small>CONDITIONAL SCENARIOS · NO PRICE TARGET INVENTED</small></div>')
            for name,text in brief['conditions']:st.markdown(f'**{name}.** {text}')
        elif brief['identified']:
            st.warning('No entry or exit levels can be supported by the available current price evidence. Rerun with verified company data before building a trade plan.')
        if brief['gaps']:
            st.markdown('#### What is missing')
            for source,detail in brief['gaps']:st.write(f'**{source}:** {detail}')
        if brief['historical']:st.warning(f'{len(brief["historical"])} older findings were excluded from the current assessment. Run a fresh investigation to reassess them.')
        plan=analysis.get('investigation_plan',{})
        lenses=plan.get('sector_lenses',[])
        with st.expander('Industry questions that could change this assessment'):
            if not lenses:st.write('Company and industry context is not established. Resolve the company identity and source gaps first.')
            for lens in lenses:
                st.markdown('**'+label(lens['name'])+'**')
                for q in lens['questions']:st.write('• '+q)
        with st.expander('Changes since the previous report'):
            delta=analysis.get('since_previous_investigation',{})
            if not delta.get('previous_as_of'):st.write('This is the first investigation. First seen by Advisor does not mean new to the market.')
            else:
                st.caption('Compared with '+delta['previous_as_of'])
                changes=delta.get('changed',[])
                if not changes:st.write('No changed source content detected. A new retrieval timestamp is not new intelligence.')
                for c in changes[:15]:st.write(f'**{c["title"]}** — {label(c["kind"])} · {c.get("period_end") or c.get("published_at") or "date unavailable"}')
        st.download_button('Download research brief',readable_markdown(report,brief),file_name=ticker+'-brief.md',mime='text/markdown')
    with evidence:
        st.caption('Each observation retains its publication, measurement and retrieval date. Older comparison periods are distinguished from current premises.')
        rows=[{'Source':label(r['source']),'Observation':r['title'],'Measured':r.get('period_end') or r.get('observed_at'),'Published':r.get('published_at'),'Retrieved':r.get('retrieved_at'),'Usability':r['temporal']['state'],'Link':r['url']} for r in lookup.values() if r['ticker']==ticker]
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True,column_config={'Link':st.column_config.LinkColumn('Original source')})
        with st.expander('Detailed calculated metrics'):
            for category in ('fundamentals','estimates','technicals'):
                values=analysis.get(category,{})
                st.markdown('**'+label(category)+'**')
                for key,value in values.items():
                    if isinstance(value,(int,float)):st.write(f'{label(key)}: {value:,.3f}')
                    elif isinstance(value,str):st.caption(f'{label(key)}: {value}')
    with sources:
        st.caption('Only sources actually queried in this run appear below. A configured route is not evidence.')
        for name,result in report.get('collection',{}).items():
            state=result.get('status','unknown')
            st.markdown(f'**{label(name)}** · {label(state)} · {result.get("records",0)} records')
            for issue in result.get('errors',[]):st.caption(issue)
        st.markdown('#### Unresolved research dimensions')
        missing=[label(c['dimension']) for c in analysis.get('coverage',[]) if c['status']!='current_inputs']
        st.write(', '.join(missing) or 'All tracked dimensions have current inputs; input coverage does not establish a conclusion.')
        st.caption(brief['basis'])
        st.write('Research rounds completed: '+str(report.get('search',{}).get('rounds_completed',0)))
        for query in report.get('search',{}).get('queries_run',[]):st.caption('Searched: '+query)
        for failure in report.get('errors',[]):st.caption('Run issue: '+str(failure))
        st.download_button('Export full audit data',json.dumps(report,indent=2),file_name=ticker+'-evidence.json',mime='application/json')


def readable_markdown(report,brief):
    if brief.get('insights'):return markdown({**report,'synthesis':{**report['synthesis'],'insights':brief['insights']}})
    lines=[f'# {report["ticker"]} — decision brief',f'As of {report["as_of"]}',f'## {brief["verdict"]}',brief['summary'],*brief['drivers']]
    for f in brief['findings']:
        meaning,check=MEANING.get(f['id'],('','Read the original source.'))
        lines += [f'### {f["title"]}',f['detail'],f'Why it matters: {meaning}',f'Next test: {check}']
        for eid in dict.fromkeys(f['evidence_ids']):
            r=brief['lookup'].get(eid)
            if r:lines.append(f'- [{r["title"] or r["source"]}]({r["url"]}) · period {r.get("period_end") or r.get("observed_at")} · published {r.get("published_at")}')
    for name,text in brief['conditions']:lines += [f'### {name}',text]
    for name,text in brief['gaps']:lines.append(f'Missing / failed — {name}: {text}')
    return '\n\n'.join(lines)
