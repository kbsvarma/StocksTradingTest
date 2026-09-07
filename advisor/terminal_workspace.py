"""Decision-first terminal with lazy workspaces and local-data-only rendering."""
from __future__ import annotations
import getpass
import json
import os
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from advisor.render_safety import text as esc, http_url
from advisor.intelligence.access import Principal, resolve_identity
from advisor.intelligence.adapters import snapshot, read_json
from advisor.intelligence.contract import number, digest
from advisor.intelligence.playbooks import PLAYBOOKS, THESIS_FIELDS, underwrite
from advisor.intelligence.forecasts import scenario_payoff
from advisor.intelligence.store import CallStore
from advisor.intelligence.terminal_data import parse_command, price_history

ET = ZoneInfo('America/New_York')
NAV = {'DESK': '01  DESK', 'CALLS': '02  CALL BOOK', 'SEC': '03  SECURITY',
       'EVID': '04  EVIDENCE', 'EVENTS': '05  CATALYSTS', 'PERF': '06  PERFORMANCE',
       'LAB': '07  RESEARCH LAB', 'OPS': '08  OPERATIONS', 'INT': '09  INVESTIGATE', 'WATCH': '10  WATCHLIST'}
ACTION = {'enter_long': 'LONG IDEA', 'hold': 'HOLD', 'reduce': 'REDUCE', 'exit': 'EXIT', 'avoid': 'AVOID', 'watch': 'WATCH'}


def fmt(value, digits=2, suffix=''):
    return f'{value:,.{digits}f}{suffix}' if number(value) else '—'


def tag(text, tone=''):
    return f'<span class="ad-tag {tone}">{esc(str(text))}</span>'


def html(text): st.markdown(text, unsafe_allow_html=True)


def heading(title, subtitle=''):
    html(f'<div class="ad-section-title">{esc(title)}</div><div class="ad-subtitle">{esc(subtitle)}</div>')


def panel(title, meta=''):
    html(f'<div class="ad-panel-head"><span>{esc(title)}</span><small>{esc(meta)}</small></div>')


def empty(title, body):
    html(f'<div class="ad-empty"><strong>{esc(title)}</strong><p>{esc(body)}</p></div>')


def stat(label, value, note):
    html(f'<div class="ad-stat"><div class="ad-stat-label">{esc(label)}</div><div class="ad-stat-value">{esc(str(value))}</div><div class="ad-stat-note">{esc(note)}</div></div>')


def navigate(page, ticker=None):
    st.session_state['ad_requested_page'] = page
    if ticker:
        st.session_state['ad_symbol'] = ticker
        st.session_state['ad_pending_symbol'] = ticker
    st.rerun()


def authorize(base):
    mode = os.environ.get('ADVISOR_AUTH_MODE', 'local')
    if mode == 'local':
        return Principal(getpass.getuser(), 'model', 'analyst'), Path(base)
    if mode != 'oidc':
        st.error('Unknown authentication mode; access blocked.'); st.stop()
    # Verified OIDC session only; no identity from query parameters or headers.
    try: identity = dict(st.user)
    except Exception: identity = {}
    if not identity.get('is_logged_in'):
        heading('Sign in to Advisor', 'Your organization controls access to research, evidence and call reviews.')
        if st.button('Sign in with your organization', type='primary'): st.login()
        st.stop()
    policy = read_json(Path(os.environ.get('ADVISOR_ACCESS_POLICY', Path(base)/'access_policy.json')))
    try: principal = resolve_identity(identity, policy)
    except PermissionError as exc:
        st.error(str(exc)); st.stop()
    root = (policy.get('tenants') or {}).get(principal.tenant, {}).get('data_root')
    if not root or not Path(root).is_absolute():
        st.error('Tenant data root is not provisioned; access blocked.'); st.stop()
    return principal, Path(root)


@st.cache_data(ttl=30, show_spinner=False)
def load_snapshot(data, user, tenant, role):
    return snapshot(Path(data), principal=Principal(user, tenant, role))


@st.cache_data(ttl=60, show_spinner=False)
def chart_data(data, ticker, sessions):
    return price_history(Path(data), ticker, sessions)


def security_chart(data, ticker, *, sessions=126, call=None, height=330):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    try: frame, meta = chart_data(str(data), ticker, sessions)
    except Exception as exc:
        empty('Price history unavailable', f'The local panel could not be verified ({type(exc).__name__}).'); return
    if frame.empty or 'close' not in frame:
        empty('No local price series', 'This security needs a verified price-panel release. No synthetic chart is substituted.'); return
    fig = make_subplots(rows=2 if 'volume' in frame else 1, cols=1, shared_xaxes=True,
                        vertical_spacing=.04, row_heights=[.8,.2] if 'volume' in frame else [1])
    if {'open', 'high', 'low', 'close'} <= set(frame):
        fig.add_trace(go.Candlestick(x=frame.index, open=frame['open'], high=frame['high'], low=frame['low'], close=frame['close'],
            increasing_line_color='#55d8b1', decreasing_line_color='#ec7789', name=ticker), row=1,col=1)
    else:
        fig.add_trace(go.Scatter(x=frame.index, y=frame['close'], mode='lines', name='Adjusted close',
            line={'color':'#64c7f5','width':1.7}, fill='tozeroy', fillcolor='rgba(47,128,173,0.08)'), row=1,col=1)
    if len(frame) >= 20:
        fig.add_trace(go.Scatter(x=frame.index, y=frame['close'].rolling(20).mean(), name='20-session mean', line={'color':'#f3ad4a','width':1}),row=1,col=1)
    if 'volume' in frame:
        colors = ['#24594d' if x >= 0 else '#58333e' for x in frame['close'].diff().fillna(0)]
        fig.add_trace(go.Bar(x=frame.index,y=frame['volume'],marker_color=colors,name='Volume'),row=2,col=1)
    if call:
        for key, color in [('stop','#ea7d8c'),('target','#56cdae'),('entry_low','#d4ab65'),('entry_high','#d4ab65')]:
            val = (call.get('plan') or {}).get(key)
            if number(val): fig.add_hline(y=val,line_color=color,line_dash='dot',line_width=1,annotation_text=key.replace('_',' '),row=1,col=1)
    low=float(frame['low'].min()) if 'low' in frame else float(frame['close'].min())
    high=float(frame['high'].max()) if 'high' in frame else float(frame['close'].max())
    fig.update_yaxes(range=[low*.96,high*1.04],row=1,col=1)
    fig.update_layout(template='plotly_dark',height=height,margin={'l':0,'r':8,'t':12,'b':0},
        paper_bgcolor='#080c12',plot_bgcolor='#0d141e',font={'family':'Menlo, monospace','size':10,'color':'#8498b0'},
        xaxis_rangeslider_visible=False,hovermode='x unified',showlegend=False,
        yaxis={'side':'right','gridcolor':'#202d3e','zeroline':False},
        xaxis={'gridcolor':'#182333','showgrid':False})
    fig.update_xaxes(rangebreaks=[dict(bounds=['sat','mon'])])
    st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False},key=f'chart_{ticker}_{sessions}_{height}')
    st.caption(f'ADJUSTED EOD · through {str(frame.index[-1])[:10]} · {meta.get("provider", "local price panel")} · chart levels are research scenarios')


def tape(s):
    candidates = {c['ticker']:c for c in s['candidates'] if c.get('ticker')}
    cells=[]
    for ticker,label in [('SPY','S&P 500'),('QQQ','NASDAQ 100'),('IWM','RUSSELL 2000'),('GLD','GOLD'),('TLT','LONG TREASURY'),('^VIX','VOLATILITY')]:
        q = s['quotes'].get(ticker,{})
        px=q.get('px')
        stamp=str(q.get('ts') or '')[:16].replace('T',' ')
        if not number(px):
            px=(candidates.get(ticker,{}).get('detail') or {}).get('px')
            stamp=str(s['freshness'].get('as_of') or '')[:10]
        status='LIVE' if q.get('status')=='live' else 'REFERENCE' if number(px) else 'NO FEED'
        previous=q.get('prev_close')
        change=(px/previous-1)*100 if number(px) and number(previous) and previous>0 else None
        move=(f'<span class="{"ad-up" if change>=0 else "ad-down"}">{"▲" if change>=0 else "▼"}{abs(change):.2f}%</span>' if change is not None else '')
        cells.append(f'<div class="ad-tape-cell"><div class="ad-tape-line"><b>{esc(ticker)}</b><strong>{fmt(px)}</strong>{move}</div><small>{esc(label)} · {status} {esc(stamp)}</small></div>')
    html('<div class="ad-strip">'+''.join(cells)+'</div>')


def candidate_table(s, *, limit=20, query='', sectors=None):
    rows=[]
    for c in s['candidates']:
        d=c.get('detail') or {}; selection=c.get('selection') or {}
        if query and query.lower() not in (c['ticker']+' '+str(d.get('sector',''))).lower(): continue
        if sectors and d.get('sector') not in sectors: continue
        rows.append({'Security':c['ticker'],'Sector':d.get('sector','Unclassified'),
                     'Reference':d.get('px'),'Signal':selection.get('score', d.get('score')),
                     'Evidence':len(c.get('buckets') or []),'Earnings':d.get('next_earnings') or 'Unverified',
                     'Discovery':', '.join(c.get('buckets') or [])})
    if not rows: empty('No matching securities','Change the filter or refresh the candidate release.'); return
    event=st.dataframe(pd.DataFrame(rows[:limit]),hide_index=True,use_container_width=True,
        height=min(440,38+35*min(limit,len(rows))),on_select='rerun',selection_mode='single-row',
        key=f'candidate_table_{limit}',column_config={
            'Reference':st.column_config.NumberColumn(format='%.2f'),
            'Signal':st.column_config.NumberColumn(help='Discovery score, not a calibrated probability',format='%.3f')})
    if event.selection.rows:
        ticker=rows[event.selection.rows[0]]['Security']
        if st.session_state.get('ad_selected_row') != ticker:
            st.session_state['ad_selected_row']=ticker; navigate('SEC',ticker)
    st.caption(f'{len(rows)} securities · select a row to inspect · reference prices from {str(s["freshness"].get("as_of") or "unknown")[:16]} · signal is not probability')


def call_card(call):
    state=call.get('market_state',call['status']).replace('_',' ').upper()
    tone='green' if call['status']=='active' else 'red' if call['status']=='review_required' else 'amber'
    thesis=call.get('thesis') or {}; text=thesis.get('what_changed') or thesis.get('why_now') or 'The thesis needs current, independently reviewed evidence.'
    plan=call.get('plan') or {}
    html(f'<div class="ad-card">{tag(ACTION.get(call["action"],"WATCH"),tone)}{tag(state)}'
         f'<h3><span class="symbol">{esc(call["ticker"])}</span> <span class="ad-dim" style="font-size:11px">{esc(call["playbook"].replace("_"," "))}</span></h3>'
         f'<p>{esc(str(text)[:350])}</p><div class="ad-card-foot">ENTRY {fmt(plan.get("entry_low"))} – {fmt(plan.get("entry_high"))} &nbsp; / &nbsp; INVALIDATION {fmt(plan.get("stop"))} &nbsp; / &nbsp; TARGET {fmt(plan.get("target"))}<br>'
         f'REV {call["revision"]} · {esc(call["issued_at"][:16])} · RESEARCH / MODEL BOOK</div></div>')


def desk(s,data):
    heading('The decision desk','New information. Clear conditions. Every call accountable to its evidence.')
    active=[c for c in s['calls'] if c['market_state'] in {'active','entry_zone','waiting','approved','conditional'}]
    review=[c for c in s['calls'] if c['market_state']=='review_required']
    cols=st.columns(4)
    values=[('Active / conditional',len(active),'Reviewed model-book calls'),('Needs reassessment',len(review),'Evidence or event review'),
            ('Discovery universe',len(s['candidates']),'Current loaded candidate slate'),('Evidence coverage',
             f'{sum(bool(c.get("evidence_audit",{}).get("ready")) for c in s["calls"])}/{len(s["calls"])}','Calls with verified claim packets')]
    for col,(label,value,note) in zip(cols,values):
        with col: stat(label,value,note)
    left,right=st.columns([2.25,1])
    with left:
        panel('PRIORITY INTELLIGENCE','MODEL BOOK / NO ORDER ROUTING')
        display=(review+active)[:3]
        if display:
            for c in display:
                call_card(c)
                if st.button(f'Inspect {c["ticker"]} →',key='desk_'+c['call_id']): navigate('EVID',c['ticker'])
        else:
            empty('No current call has cleared the review process',
                  'The desk is preserving the distinction between a ranked candidate and a reviewed decision. Inspect the opportunity set below, build an evidence packet, and follow its review here.')
        panel('OPPORTUNITY MONITOR','SELECT SECURITY TO DRILL DOWN')
        candidate_table(s,limit=10)
    with right:
        panel('SESSION BRIEF',datetime.now(ET).strftime('%a %d %b').upper())
        reg=s['signals'].get('regime') or {}
        if isinstance(reg,str):reg={'name':reg}
        html(f'<div class="ad-card" style="border-left-color:#5ba7d0"><div class="ad-kicker">MARKET CONTEXT</div><h3>{esc(str(reg.get("name") or "Unclassified").replace("_"," ").title())}</h3><p>VIX {fmt(reg.get("vix"))} · term structure {fmt(reg.get("vix_term"))}</p><div class="ad-card-foot">AS OF {esc(str(s["signals"].get("as_of") or "Unavailable")[:16])}<br>Descriptive regime; no return forecast implied.</div></div>')
        panel('WHAT NEEDS ATTENTION','INPUTS / REVIEW / COVERAGE')
        notes=[]
        if s['freshness']['status']!='current': notes.append(('Research release',f'{s["freshness"]["status"].title()}: {s["freshness"]["reason"]}'))
        if not any(q['status']=='live' for q in s['quotes'].values()): notes.append(('Live quotes','No current-session live quote available. Reference prices remain labeled.'))
        if not s['performance']['groups']: notes.append(('Track record','No cost-complete prospective cohort is available for this call engine.'))
        notes += [('Source integrity',i) for i in s['issues'][:3]]
        for title,body in notes: html(f'<div class="ad-list-item"><b>{esc(title)}</b><br>{esc(body)}</div>')
        panel('RESEARCH PLAYBOOKS','REGISTERED HYPOTHESES')
        for i,(key,p) in enumerate(PLAYBOOKS.items(),1):
            html(f'<div class="ad-list-item"><span class="ad-amber">0{i}</span> &nbsp;<b>{esc(p["name"])}</b><br><small>{p["horizon"]} SESSIONS · v{p["version"]} · awaiting prospective validation</small></div>')
        if st.button('Open underwriting lab →',key='desk_lab',use_container_width=True): navigate('LAB')


def choose_call(s,ticker=None):
    calls=[c for c in s['calls'] if not ticker or c['ticker']==ticker]
    if not calls: return None
    return st.selectbox('CALL EPISODE',calls,format_func=lambda c:f'{c["ticker"]} · {c["playbook"]} · {c["issued_at"][:10]} · r{c["revision"]}',key='call_episode')


def calls_page(s,data):
    heading('Call book','One episode, one history. Entry observations are distinguished from actual executions.')
    a,b,c=st.columns([1.2,1.2,2])
    with a: states=st.multiselect('STATE',sorted({x['market_state'] for x in s['calls']}),key='call_states')
    with b: origin=st.selectbox('ORIGIN',['All','underwriting','brief','suggestion'])
    with c: query=st.text_input('FILTER SECURITY',key='calls_query',placeholder='Ticker or playbook')
    calls=[x for x in s['calls'] if (not states or x['market_state'] in states) and (origin=='All' or x['origin']==origin)
           and (not query or query.lower() in (x['ticker']+' '+x['playbook']).lower())]
    if not calls: empty('No calls match this view','Adjust your filters or inspect the research lab for candidates awaiting underwriting.'); return
    rows=[{'Ticker':x['ticker'],'Intent':ACTION.get(x['action']),'State':x['market_state'].replace('_',' '),
           'Playbook':x['playbook'],'Entry low':x['plan'].get('entry_low'),'Entry high':x['plan'].get('entry_high'),
           'Invalidation':x['plan'].get('stop'),'Target':x['plan'].get('target'),'Expiry':x['expires_at'][:10],
           'Revision':x['revision'],'Source':x['origin']} for x in calls]
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    st.download_button('Export displayed call book',json.dumps(calls,indent=2),file_name='advisor-call-book.json',mime='application/json')
    panel('EPISODE INSPECTOR')
    selected=st.selectbox('SELECT CALL',calls,format_func=lambda x:f'{x["ticker"]} · {x["call_id"][:8]} · {x["status"]}')
    call_card(selected)
    from advisor.intelligence.performance import portfolio_impact
    impact=portfolio_impact(selected,s.get('portfolio_context') or {})
    with st.expander('Incremental portfolio exposure / mandate checks'):
        if impact['scope']=='model_book_only':
            st.caption('Portfolio-specific allocation unavailable. The call remains a model-book research idea.')
        st.json(impact)
    if st.button('Inspect evidence and revision history →'): navigate('EVID',selected['ticker'])


def security_page(s,data,ticker):
    if ticker and st.button('Investigate this security',key='investigate_security'):
        st.session_state['investigator_ticker']=ticker
        navigate('INT',ticker)
    if not ticker: empty('Select a security','Enter a covered ticker in the command line, or select a security from the opportunity monitor.'); return
    candidate=next((c for c in s['candidates'] if c['ticker']==ticker),{})
    detail=candidate.get('detail') or {}; quote=s['quotes'].get(ticker,{})
    calls=[c for c in s['calls'] if c['ticker']==ticker]
    heading(ticker,detail.get('sector') or 'Security workspace · research coverage')
    a,b,c,d=st.columns(4)
    with a:stat('Observed / reference price',fmt(quote.get('px',detail.get('px'))),quote.get('status','candidate reference').upper())
    with b:stat('Discovery signals',len(candidate.get('buckets') or []),'May share the same evidence family')
    with c:stat('Call episodes',len(calls),'Current and historical coverage')
    with d:stat('Next earnings',str(detail.get('next_earnings') or 'Unverified')[:10],'Calendar estimate; verify issuer timing')
    left,right=st.columns([2.4,1])
    with left:
        panel('PRICE / SCENARIO LEVELS','LOCAL IMMUTABLE PANEL')
        span=st.radio('CHART WINDOW',[('1M',22),('3M',63),('6M',126),('1Y',252)],format_func=lambda x:x[0],horizontal=True,index=2,key='chart_span',label_visibility='collapsed')
        security_chart(data,ticker,sessions=span[1],call=calls[0] if calls else None,height=385)
        panel('EXPECTATIONS GAP','WHAT MUST BE TRUE')
        thesis=calls[0].get('thesis') or {} if calls else {}
        for key,label in [('what_changed','01 · What changed'),('consensus','02 · Consensus expects'),('variant','03 · Our variant'),('mechanism','04 · Economic mechanism'),('why_not_priced','05 · Why the gap persists')]:
            html(f'<div class="ad-list-item"><b>{label}</b><br>{esc(thesis.get(key) or "Not underwritten. This field needs dated evidence.")}</div>')
    with right:
        panel('DECISION CONTEXT')
        if calls:call_card(calls[0])
        else:empty('Discovery only','This security is on the candidate slate. A rank is not a published call.')
        if st.button('Open evidence dossier →',use_container_width=True):navigate('EVID',ticker)
        pinned=st.session_state.setdefault('ad_watch',[])
        if st.button('Remove from session watch' if ticker in pinned else '+ Add to session watch',use_container_width=True):
            st.session_state['ad_watch']=[x for x in pinned if x!=ticker] if ticker in pinned else pinned+[ticker];st.rerun()
        panel('SIGNAL DECOMPOSITION','RESEARCH INPUTS')
        for name in candidate.get('buckets') or []:
            html(f'<div class="ad-list-item"><b>{esc(name.replace("_"," ").title())}</b><br><small>Candidate generator · unvalidated contribution</small></div>')
        if calls:
            panel('INVALIDATION / OPPOSING EVIDENCE')
            for key in ('invalidation','contrary_evidence','catalyst'):
                value=(calls[0].get('thesis') or {}).get(key)
                if value: html(f'<div class="ad-list-item"><small>{key.upper().replace("_"," ")}</small><br>{esc(value)}</div>')
            scenario=calls[0].get('scenario_analysis') or {}
            if scenario:
                panel('SCENARIO ECONOMICS','ASSUMPTIONS / AFTER MODELED COSTS')
                html(f'<div class="ad-list-item">Weighted scenario payoff <b>{fmt(scenario.get("weighted_net_return_pct"),2,"%")}</b><br>'
                     f'Adverse sensitivity {fmt(scenario.get("stress_net_return_pct"),2,"%")}<br><small>Analyst weights; not a calibrated forecast.</small></div>')


def evidence_page(s,data,ticker,principal):
    heading('Evidence dossier',f'{ticker or "Select a security"} · inspect the source, the claim, and every decision revision.')
    call=choose_call(s,ticker)
    if not call:
        empty('No published evidence packet for this security','Open Research Lab to prepare an expectations-gap packet. Existing narrative research is available below when present.')
    else:
        call_card(call)
        for blocker in call.get('blockers') or []: st.warning(blocker)
        a,b=st.columns([2.2,1])
        with a:
            panel('LOAD-BEARING CLAIMS','SOURCE-BOUND ≠ INDEPENDENTLY VERIFIED')
            claims=call.get('claims') or []
            if not claims: empty('Evidence packet is incomplete','No independently reviewable claim records have been attached to this call.')
            for i,claim in enumerate(claims):
                src=(call.get('source_snapshot') or {}).get(claim.get('source_id')) or {}
                verified=next((r for r in (call.get('evidence_audit') or {}).get('claims',[]) if r.get('claim_id')==claim.get('claim_id')), {})
                url=http_url(src.get('url') or claim.get('url'))
                body=claim.get('excerpt') or claim.get('claim') or claim.get('field') or 'Claim text unavailable'
                html(f'<div class="ad-evidence">{tag(verified.get("status","legacy evidence").upper(),"green" if verified.get("status")=="verified" else "amber")} {tag(src.get("tier","source tier unverified"))}'
                     f'<blockquote>{esc(body)}</blockquote><span class="ad-mono ad-dim">{esc(claim.get("period", ""))} · {esc(claim.get("unit", ""))} · {esc(str(src.get("retrieved_at") or claim.get("retrieved") or "time unavailable"))}</span></div>')
                if url:st.markdown(f'[Open source ↗]({url})')
                if src:
                    with st.expander(f'Snapshot / verification details {i+1}'):
                        st.code(src.get('content',''),language=None)
                        st.json({'source_hash':src.get('sha256'),'review':claim.get('review'),'checks':verified})
        with b:
            panel('REVIEW TRAIL')
            database=data/'intelligence'/'calls.sqlite'
            history=[]
            if database.exists():
                try:
                    with CallStore(database,principal,readonly=True) as store:history=store.history(call['call_id'])
                except Exception as exc:st.error(f'History unavailable: {type(exc).__name__}')
            for revision in history or [call]:
                html(f'<div class="ad-list-item"><b>r{revision["revision"]} · {esc(revision["status"].replace("_"," "))}</b><br>{esc(revision.get("change_reason") or "Original episode")}'
                     f'<br><small>{esc(revision.get("updated_at") or revision["issued_at"])}</small></div>')
            st.download_button('Export this evidence packet',json.dumps(call,indent=2),file_name=f'{call["ticker"]}-{call["call_id"][:8]}.json',mime='application/json')
            if principal.role in {'reviewer','admin'} and call['origin']=='underwriting' and call['status']=='review_required':
                panel('INDEPENDENT REVIEW')
                acknowledged=st.checkbox('I reviewed the source excerpts, thesis and plan',key='review_ack')
                reason=st.text_area('REVIEW RATIONALE',key='review_reason')
                x,y=st.columns(2)
                verdict=None
                with x:
                    if st.button('Approve research',disabled=not acknowledged or not reason.strip()):verdict='approve'
                with y:
                    if st.button('Reject',disabled=not acknowledged or not reason.strip()):verdict='reject'
                if verdict:
                    try:
                        with CallStore(database,principal) as store:store.review(call['call_id'],expected_revision=call['revision'],verdict=verdict,reason=reason)
                        st.cache_data.clear();st.rerun()
                    except (PermissionError,ValueError) as exc:st.error(str(exc))
    if ticker:
        dossier=data/'knowledge'/'dossiers'/ticker/'narrative.md'
        if dossier.exists():
            with st.expander('Historical analyst dossier · opinions require re-verification'):
                st.markdown(dossier.read_text().replace('$',r'\$'))


def events_page(s,data):
    heading('Catalyst monitor','Events are routed to the assumptions and call episodes they can change.')
    rows=[]
    for e in s['events']:
        affected=[c['ticker'] for c in s['calls'] if c['ticker']==e.get('ticker') and c['status'] not in {'expired','resolved','withdrawn'}]
        rows.append({'Observed':e.get('ts'),'Ticker':e.get('ticker'),'Event':e.get('form') or e.get('kind'),
                     'Source':e.get('src'),'Calls affected':len(affected),'URL':http_url(e.get('url'))})
    if rows: st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True,column_config={'URL':st.column_config.LinkColumn('Source document')})
    else:empty('No filing alerts in the current data root','The event worker watches published calls for filings, guidance, estimate revisions and price conditions. Event ingestion health is visible in Operations.')
    panel('FORWARD CALENDAR','PROVIDER ESTIMATES / NOT ISSUER CONFIRMATION')
    dates=[]
    for c in s['candidates']:
        date=(c.get('detail') or {}).get('next_earnings')
        if date: dates.append({'Date':str(date)[:10],'Ticker':c['ticker'],'Event':'Earnings','Timing':'Unverified estimate',
                               'State':'Past / calendar needs refresh' if str(date)[:10] < datetime.now(ET).date().isoformat() else 'Upcoming estimate'})
    if dates:st.dataframe(pd.DataFrame(sorted(dates,key=lambda r:r['Date'])),hide_index=True,use_container_width=True)
    panel('REASSESSMENT ACTIVITY','LATEST WORKER RUN')
    changes=s['worker'].get('changes') or []
    if changes:st.dataframe(pd.DataFrame(changes),hide_index=True,use_container_width=True)
    else:st.caption('No reassessment transitions recorded in the latest worker run.')


def performance_page(s,data):
    heading('Decision performance','Measure the call that was issued, the entry that was possible, and the alternatives it needed to beat.')
    coverage=s['performance']['coverage'];cols=st.columns(4)
    for col,label,value,note in [(cols[0],'Prospective episodes',coverage.get('episodes',0),'Distinct episode identities'),
        (cols[1],'Activated',coverage.get('activated',0),'Observed or modeled entry'),
        (cols[2],'Unresolved',coverage.get('unresolved',0),'Remain visible in the denominator'),
        (cols[3],'Cost-complete cohorts',len(s['performance']['groups']),'Separated by model and outcome basis')]:
        with col:stat(label,value,note)
    groups=s['performance']['groups']
    if groups:
        panel('COHORT ATTRIBUTION','PROSPECTIVE / AFTER COSTS')
        st.dataframe(pd.DataFrame([{k:v for k,v in r.items() if k not in {'note','promotion_allowed'}} for r in groups]),hide_index=True,use_container_width=True)
    else:empty('The new engine has no demonstrated prospective track record yet',
               'Historical research and passing software tests do not establish an investment edge. New episodes retain their entry policy, costs, benchmark window and model version so results can accumulate without rewriting history.')
    a,b=st.columns(2)
    with a:
        panel('WHAT COUNTS')
        for title,body in [('Signal returns','Was the candidate direction informative?'),('Published-policy simulation','What would the exact entry and exit policy have produced after modeled costs?'),('Observed model book','Which live quote conditions were actually observed? These are not broker fills.'),('Actual execution','Only externally supplied verified fills can establish realized customer performance.')]:
            html(f'<div class="ad-list-item"><b>{title}</b><br>{body}</div>')
    with b:
        panel('CALIBRATION / MODEL GOVERNANCE')
        cal=s['calibration']
        html(f'<div class="ad-card"><div class="ad-kicker">LEGACY CALIBRATION SNAPSHOT</div><h3>{esc(str(cal.get("n_explicit_scored",cal.get("n_resolved_scored",0))))} scored observations</h3><p>Brier {fmt(cal.get("brier"),4)} · as of {esc(str(cal.get("as_of") or "unavailable")[:16])}</p><div class="ad-card-foot">Legacy pooled calibration does not promote a new playbook. New evaluations use time-held-out, cohort-specific evidence.</div></div>')
        st.json(coverage,expanded=False)
    st.download_button('Export performance diagnostics',json.dumps(s['performance'],indent=2),file_name='advisor-performance.json',mime='application/json')


def lab_page(s,data,principal,legacy):
    heading('Research lab','A workbench for falsifiable theses, independent evidence and explicit scenario assumptions.')
    work=st.selectbox('WORKBENCH',['Opportunity screener','Thesis builder','Underwriting packet','Scenario sensitivity','Legacy research tools'])
    if work=='Opportunity screener':
        a,b=st.columns(2)
        with a:query=st.text_input('SEARCH',placeholder='Ticker or sector',key='screen_query')
        with b:sectors=st.multiselect('SECTOR',sorted({(c.get('detail') or {}).get('sector') or 'Unclassified' for c in s['candidates']}))
        candidate_table(s,limit=100,query=query,sectors=sectors)
    elif work=='Thesis builder':
        panel('DRAFT A DECISION','HYPOTHESIS → EVIDENCE → INDEPENDENT REVIEW')
        symbols=sorted({c['ticker'] for c in s['candidates']})
        if not symbols:empty('Coverage is not loaded','Load a candidate release before drafting a security thesis.');return
        with st.form('thesis_builder'):
            a,b=st.columns(2)
            with a:
                ticker=st.selectbox('SECURITY TO UNDERWRITE',symbols)
                event_date=st.date_input('CATALYST / EVENT DATE',value=datetime.now(ET).date())
            with b:
                playbook=st.selectbox('THESIS PLAYBOOK',list(PLAYBOOKS),format_func=lambda x:PLAYBOOKS[x]['name'])
                event_time=st.time_input('EVENT TIME (NEW YORK)',value=time(16,30))
            thesis={}
            prompts={'what_changed':'What changed?','consensus':'What does consensus expect?',
                     'variant':'What do you believe differently?','mechanism':'How does this change earnings or cash flow?',
                     'why_not_priced':'Why might the price not reflect it?','catalyst':'What closes the gap, and when?',
                     'invalidation':'What would prove the thesis wrong?','contrary_evidence':'What is the strongest opposing evidence?'}
            a,b=st.columns(2)
            for i,(field,label) in enumerate(prompts.items()):
                with a if i%2==0 else b:
                    thesis[field]=st.text_area(label,height=85,key='thesis_'+field)
            submitted=st.form_submit_button('Save research hypothesis',use_container_width=True,
                                            disabled=principal.role not in {'analyst','admin'})
        if submitted:
            if len(thesis['what_changed'].strip())<12:
                st.error('Describe the specific change before saving the hypothesis.')
            else:
                now=datetime.now(ET)
                packet={'ticker':ticker,'episode':f'user-thesis:{principal.user}:{now.isoformat()}',
                    'author':principal.user,'playbook':playbook,'decision_at':now.isoformat(),
                    'event_at':datetime.combine(event_date,event_time,tzinfo=ET).isoformat(),
                    'expires_at':(now+timedelta(days=60)).isoformat(),'thesis':thesis,
                    'claims':[],'sources':{},'observations':{},'plan':{},'submission_kind':'analyst_hypothesis'}
                result=underwrite(packet,as_of=now.isoformat())
                with CallStore(data/'intelligence'/'calls.sqlite',principal) as store:store.put(result)
                from advisor.research.suggestion_store import atomic_json
                atomic_json(data/'intelligence'/'inbox'/f'{result["call_id"]}.json',packet)
                st.cache_data.clear()
                st.success('Hypothesis saved with explicit evidence gaps. It is available to the next synthesis run and cannot activate without a plan and independent review.')
                st.download_button('Download hypothesis',json.dumps(packet,indent=2),file_name=f'{ticker}-hypothesis.json',mime='application/json')
    elif work=='Underwriting packet':
        spec=st.selectbox('PLAYBOOK',list(PLAYBOOKS),format_func=lambda x:PLAYBOOKS[x]['name'])
        a,b=st.columns([1.25,1])
        with a:
            panel('REQUIRED EVIDENCE',f'VERSION {PLAYBOOKS[spec]["version"]}')
            for name in PLAYBOOKS[spec]['required']:html(f'<div class="ad-list-item">{esc(name.replace("_"," ").title())}</div>')
            st.caption(PLAYBOOKS[spec]['benchmark'])
        with b:
            panel('EXPECTATIONS-GAP CONTRACT')
            for key in THESIS_FIELDS:html(f'<div class="ad-list-item">{esc(key.replace("_"," ").title())}</div>')
        upload=st.file_uploader('Import a complete JSON evidence packet',type=['json'])
        if upload:
            try:
                packet=json.loads(upload.getvalue())
                result=underwrite(packet,as_of=datetime.now(ET).isoformat())
                st.json(result,expanded=False)
                for blocker in result['blockers']:st.warning(blocker)
                if principal.role in {'analyst','admin'} and st.button('Save underwriting draft',type='primary'):
                    result['submitted_by']=principal.user
                    with CallStore(data/'intelligence'/'calls.sqlite',principal) as store:
                        old=store.latest(result['call_id'])
                        if old:raise ValueError('This episode already exists. Submit a versioned revision through the worker inbox.')
                        store.put(result)
                    st.cache_data.clear();st.success('Draft saved. Independent review is required before activation.')
            except (ValueError,KeyError,TypeError,PermissionError) as exc:st.error(str(exc))
        with st.expander('Packet specification / local worker'):
            st.code('python -m advisor.intelligence.worker --data-dir /path/to/advisor/data',language='bash')
            st.markdown('Packets contain ticker, episode, playbook, event_at, expires_at, author, claims, sources, observations, thesis and plan. The worker reads intelligence/inbox/*.json. Source snapshots and claim reviews must bind to the exact hashes. No packet can approve itself.')
    elif work=='Scenario sensitivity':
        panel('SCENARIO PAYOFF','ILLUSTRATIVE INPUTS / NOT A TRADE RECOMMENDATION')
        a,b,c,d=st.columns(4)
        with a:entry=st.number_input('ASSUMED ENTRY',min_value=.01,value=100.,step=1.)
        with b:adverse=st.number_input('ADVERSE EXIT',min_value=.01,value=90.,step=1.)
        with c:base=st.number_input('BASE EXIT',min_value=.01,value=105.,step=1.)
        with d:favorable=st.number_input('FAVORABLE EXIT',min_value=.01,value=120.,step=1.)
        a,b,c=st.columns(3)
        with a:wa=st.slider('ADVERSE WEIGHT %',0,100,30)
        with b:wb=st.slider('BASE WEIGHT %',0,100,40)
        with c:cost=st.number_input('ROUND TRIP COST BPS',min_value=0.,value=20.)
        if wa+wb>100:st.error('Adverse and base weights exceed 100%.');return
        result=scenario_payoff(entry=entry,round_trip_bps=cost,scenarios=[{'name':'adverse','exit_px':adverse,'weight':wa/100},
            {'name':'base','exit_px':base,'weight':wb/100},{'name':'favorable','exit_px':favorable,'weight':(100-wa-wb)/100}])
        cols=st.columns(3)
        with cols[0]:stat('Weighted scenario return',fmt(result['weighted_net_return_pct'],2,'%'),'Assumption-weighted, uncalibrated')
        with cols[1]:stat('After 2pp adverse shift',fmt(result['stress_net_return_pct'],2,'%'),'Sensitivity to assumption error')
        with cols[2]:stat('Worst modeled scenario',fmt(result['worst_scenario_pct'],2,'%'),'Not a guaranteed maximum loss')
        import plotly.graph_objects as go
        fig=go.Figure(go.Bar(x=[r['name'].title() for r in result['scenarios']],y=[r['net_return_pct'] for r in result['scenarios']],marker_color=['#dc7186','#ffb449','#4ac6a5']))
        fig.update_layout(height=270,paper_bgcolor='#080c12',plot_bgcolor='#101720',font={'color':'#a4b4c8'},margin={'t':10,'b':20,'l':10,'r':10},yaxis_title='Net return %')
        st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False});st.caption(result['note'])
    else:
        if not legacy:
            st.info('Legacy operator workspaces are unavailable in this tenant.');return
        tool=st.selectbox('RESEARCH TOOL',list(legacy))
        panel(tool.upper(),'EXISTING RESEARCH WORKSPACE')
        legacy[tool]()


def operations_page(s,data,principal,controls):
    heading('Operations & governance','Publication integrity, worker health and recoverable decision history.')
    a,b=st.columns(2)
    with a:
        panel('INTELLIGENCE WORKER')
        if s['worker']:st.json(s['worker'],expanded=True)
        else:empty('Worker has not published status','The command desk can read historical artifacts. Run the intelligence worker to ingest new packets and process events.')
        panel('DATA DIAGNOSTICS')
        st.json({'freshness':s['freshness'],'issues':s['issues']})
    with b:
        panel('ACCESS & AUDIT')
        html(f'<div class="ad-card"><div class="ad-kicker">{esc(principal.tenant.upper())}</div><h3>{esc(principal.role.title())} workspace</h3><p>{esc(principal.user)}</p><div class="ad-card-foot">Tenant-scoped call history · independent reviews · append-only revisions<br>Execution remains disabled.</div></div>')
        db=data/'intelligence'/'calls.sqlite'
        if db.exists():
            try:
                with CallStore(db,principal,readonly=True) as store:export=store.export()
                st.download_button('Export verified audit checkpoint',json.dumps(export,indent=2),file_name='advisor-audit.json',mime='application/json')
                st.caption('Retain exported checkpoints independently. Local hashes do not prevent an administrator replacing the entire store.')
            except Exception as exc:st.error(f'Audit validation failed: {type(exc).__name__}: {exc}')
        with st.expander('Enterprise provisioning'):
            st.markdown('OIDC mode requires a configured identity provider, verified-email claims and an access policy mapping members to roles and explicit tenant data roots. Local mode has analyst permissions; it cannot independently approve calls. Data licensing and external identity-provider provisioning remain deployment requirements.')
    if principal.tenant=='model' and principal.role in {'analyst','admin'}:
        panel('RESEARCH GENERATION','EXISTING CONTROLLED PIPELINE')
        controls()


def render_terminal(base, *, legacy, controls):
    principal,data=authorize(base)
    html('<style>'+Path(__file__).with_name('terminal_theme.css').read_text()+'</style>')
    s=load_snapshot(str(data),principal.user,principal.tenant,principal.role)
    now=datetime.now(ET)
    html(f'<div class="ad-header"><div class="ad-brand"><span>ADVISOR TERMINAL</span><small>MARKET INTELLIGENCE · RESEARCH CONSOLE</small></div>'
         f'<div class="ad-header-right">{tag("MODEL BOOK · RESEARCH","amber")}{tag(principal.tenant.upper())}<br>{now.strftime("%a %d %b %Y · %H:%M:%S ET").upper()}</div></div>')
    tape(s)
    symbols=sorted({c['ticker'] for c in s['candidates']}|{c['ticker'] for c in s['calls']})
    requested=st.session_state.pop('ad_requested_page',None)
    if requested in NAV:st.session_state['ad_nav']=NAV[requested]
    if 'ad_symbol' not in st.session_state:st.session_state['ad_symbol']=symbols[0] if symbols else None
    workspace_key=f'{principal.tenant}:{principal.user}:{data}'
    if st.session_state.get('ad_workspace_loaded')!=workspace_key:
        saved={}
        db=data/'intelligence'/'calls.sqlite'
        if db.exists():
            try:
                with CallStore(db,principal,readonly=True) as store:saved=store.workspace()
            except Exception:pass
        st.session_state['ad_watch']=[t for t in saved.get('watch',[]) if t in symbols]
        if saved.get('symbol') in symbols:st.session_state['ad_symbol']=saved['symbol']
        st.session_state['ad_workspace_loaded']=workspace_key
    st.markdown('<div class="ad-control-label">Search a stock</div>',unsafe_allow_html=True)
    with st.form('stock_search',clear_on_submit=False):
        search_col, search_button = st.columns([8,1],vertical_alignment='bottom')
        with search_col:
            stock_query=st.text_input('Search a stock',placeholder='Ticker or company · NVDA, NVIDIA, MSFT',key='ad_stock_search',label_visibility='collapsed')
        with search_button:
            search_submitted=st.form_submit_button('Investigate →',use_container_width=True,type='primary')
    if search_submitted:
        from advisor.investigator.presentation import resolve_query
        try:
            searched=resolve_query(stock_query,data)
            st.session_state['ad_search_choices']=[]
            st.session_state['ad_nav']=NAV['INT']
            st.session_state['ad_symbol']=searched
            st.session_state['investigator_ticker']=searched
            st.session_state['ad_market_requested']=searched
            st.session_state['ad_investigate_requested']=searched
        except ValueError as exc:
            st.warning(str(exc))
            st.session_state['ad_search_choices']=getattr(exc,'choices',[])
    choices=st.session_state.get('ad_search_choices',[])
    if choices:
        picked=st.selectbox('Matching companies',choices,format_func=lambda r:f"{r['name']} · {r['ticker']} · {r['exchange']}")
        if st.button('Open selected company'):
            st.session_state['ad_nav']=NAV['INT']
            st.session_state['ad_symbol']=picked['ticker']
            st.session_state['investigator_ticker']=picked['ticker']
            st.session_state['ad_market_requested']=picked['ticker']
            st.session_state['ad_investigate_requested']=picked['ticker']
            st.session_state['ad_search_choices']=[]
            st.rerun()
    with st.expander('Terminal commands & workspace controls'):
        with st.form('command_line',clear_on_submit=True):
            x,y,z,w=st.columns([6,1,1.5,1.5],vertical_alignment='center')
            with x:command=st.text_input('COMMAND',placeholder='NVDA DES · CALLS · EVENTS · HELP',label_visibility='collapsed')
            with y:submitted=st.form_submit_button('GO ↵',use_container_width=True)
            with z:refresh_pressed=st.form_submit_button('↻ Refresh data',use_container_width=True)
            with w:save_pressed=st.form_submit_button('Save workspace',use_container_width=True,disabled=principal.role not in {'analyst','admin'})
        if submitted:
            target,security,error=parse_command(command,set(symbols))
            if error:st.warning(error)
            elif target=='HELP':st.info('TICKER INT investigates a stock. DES opens its security page. CALLS, EVENTS, PERF, LAB, OPS and WATCH switch workspaces.')
            elif target:
                st.session_state['ad_nav']=NAV[target]
                if security:
                    st.session_state['ad_symbol']=security
                    st.session_state['ad_pending_symbol']=security
                    if target=='INT':st.session_state['investigator_ticker']=security
        if refresh_pressed:
            st.session_state['ad_requested_page']=next((k for k,v in NAV.items() if v==st.session_state.get('ad_nav')),'DESK')
            st.cache_data.clear();st.rerun()
        if save_pressed:
            with CallStore(data/'intelligence'/'calls.sqlite',principal) as store:
                store.save_workspace({'watch':st.session_state.get('ad_watch',[]),'symbol':st.session_state.get('ad_symbol')})
            st.toast('Workspace saved')
    nav=st.radio('WORKSPACE',list(NAV.values()),horizontal=True,label_visibility='collapsed',key='ad_nav')
    page=next(k for k,v in NAV.items() if v==nav)
    if s['freshness']['status']!='current' and page!='INT':
        html(f'<div class="ad-alert"><b>ARCHIVE / INCOMPLETE COVERAGE</b> &nbsp; Research as of {esc(str(s["freshness"].get("as_of") or "unavailable")[:16])}. {esc(s["freshness"]["reason"])}. Historical context remains visible; it is not a current call.</div>')
    if page in {'SEC','EVID'} and symbols:
        pending_symbol=st.session_state.pop('ad_pending_symbol',None)
        if pending_symbol in symbols:st.session_state['security_picker']=pending_symbol
        idx=symbols.index(st.session_state['ad_symbol']) if st.session_state['ad_symbol'] in symbols else 0
        symbol=st.selectbox('SECURITY',symbols,index=idx,key='security_picker')
        st.session_state['ad_symbol']=symbol
    ticker=st.session_state.get('ad_symbol')
    if page=='DESK':desk(s,data)
    elif page=='CALLS':calls_page(s,data)
    elif page=='SEC':security_page(s,data,ticker)
    elif page=='EVID':evidence_page(s,data,ticker,principal)
    elif page=='EVENTS':events_page(s,data)
    elif page=='PERF':performance_page(s,data)
    elif page=='LAB':lab_page(s,data,principal,legacy if principal.tenant=='model' and data.resolve()==Path(base).resolve() else {})
    elif page=='OPS':operations_page(s,data,principal,controls)
    elif page=='WATCH':
        from advisor.watchlist_workspace import render
        render(data,principal)
    elif page=='INT':
        from advisor.investigator.workspace import render
        render(data,principal,ticker)
    pinned=st.session_state.get('ad_watch') or []
    if pinned:st.caption('SESSION WATCH  ·  '+'  /  '.join(pinned))
    html(f'<div class="ad-foot"><span>ADVISOR / {esc(page)} &nbsp;·&nbsp; {len(s["candidates"])} CANDIDATES &nbsp;·&nbsp; {len(s["calls"])} EPISODES</span><span>RESEARCH ONLY · NO ORDER ROUTING &nbsp; / &nbsp; {now.strftime("%H:%M ET")}</span></div>')
