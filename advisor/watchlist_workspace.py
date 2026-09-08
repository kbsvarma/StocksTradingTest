"""Per-user persistent watchlists with dated market and research summaries."""
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
import pandas as pd
import streamlit as st
from advisor.intelligence.store import CallStore
from advisor.market_data import load,refresh,refresh_week,frame,watch_symbols,save_symbols,intraday_due
from advisor.market_view import fmt,clock,figure,selected_history
from advisor.investigator.search import resolve,AmbiguousCompany
from advisor.investigator.engine import load_latest
from advisor.investigator.presentation import decision_brief


def watch_chart(bars, period='3M', *, snapshot=None):
    """The same price-scaled, shaded chart used by Investigate."""
    closes=pd.to_numeric(bars.Close,errors='coerce').dropna().sort_index()
    closes=closes[(closes>0) & (closes<float('inf'))]
    if closes.empty:return None,None
    snapshot=snapshot or {'ticker':'Price','bars':[{'Date':d.isoformat(),'Close':float(v)} for d,v in closes.items()]}
    visible,intraday=selected_history(snapshot,period)
    first,last=float(visible.Close.iloc[0]),float(visible.Close.iloc[-1])
    chart=figure(snapshot,period=period)
    chart.update_layout(height=240,margin=dict(l=4,r=26,t=10,b=22),font_size=11)
    chart.update_yaxes(nticks=4,fixedrange=True)
    chart.update_xaxes(fixedrange=True)
    return chart,{'change':last-first,'percent':(last/first-1)*100,
        'color':'#81c995' if last>=first else '#f28b82','start':visible.index[0],
        'end':visible.index[-1],'intraday':intraday}


def render(data,principal):
    st.markdown('## Watchlist')
    st.caption('Your securities, market context and latest research in one place. Changes are saved to your workspace.')
    with CallStore(Path(data)/'intelligence/calls.sqlite',principal) as store:
        symbols=watch_symbols(store)
        if not store.workspace('watchlist') and principal.role in {'analyst','admin'}:save_symbols(store,symbols)
    st.markdown('<div class="ad-control-label">Add a company or ticker</div>',unsafe_allow_html=True)
    with st.form('watch_add'):
        a,b=st.columns([5,1],vertical_alignment='bottom')
        with a:q=st.text_input('Add a company or ticker',placeholder='NVIDIA, Apple, MSFT, GOOGL',label_visibility='collapsed')
        with b:add=st.form_submit_button('Add to watchlist',type='primary',use_container_width=True,disabled=principal.role not in {'analyst','admin'})
    if add:
        try:
            ticker=resolve(q,data)
            with CallStore(Path(data)/'intelligence/calls.sqlite',principal) as store:symbols=save_symbols(store,[*symbols,ticker])
            with st.spinner('Loading '+ticker):refresh(data,ticker)
            st.rerun()
        except AmbiguousCompany as exc:st.session_state['watch_choices']=exc.choices
        except ValueError as exc:st.warning(str(exc))
    choices=st.session_state.get('watch_choices',[])
    if choices:
        picked=st.selectbox('Choose a listing',choices,format_func=lambda r:f'{r["name"]} · {r["ticker"]} · {r["exchange"]}')
        if st.button('Add selected listing'):
            with CallStore(Path(data)/'intelligence/calls.sqlite',principal) as store:save_symbols(store,[*symbols,picked['ticker']])
            refresh(data,picked['ticker']);st.session_state['watch_choices']=[];st.rerun()
    a,b=st.columns([1,3],vertical_alignment='bottom')
    with a:
        if st.button('↻ Refresh watchlist',use_container_width=True):
            with st.spinner('Refreshing market snapshots…'):
                with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda t:refresh(data,t),symbols))
            st.rerun()
    with b:
        with st.expander('Manage tickers'):
            remove=st.multiselect('Remove from watchlist',symbols)
            if st.button('Remove selected',disabled=not remove or principal.role not in {'analyst','admin'}):
                with CallStore(Path(data)/'intelligence/calls.sqlite',principal) as store:save_symbols(store,[t for t in symbols if t not in remove])
                st.rerun()
    if not symbols:st.info('Your watchlist is empty. Add a company above.');return
    period=st.segmented_control("Chart period",["1W","1M","3M"],default="3M",selection_mode="single",key="watch_chart_period") or "3M"
    if period in {'1W','1M'}:
        pending=[ticker for ticker in symbols if intraday_due(load(data,ticker))]
        if pending:
            with st.spinner('Loading detailed price charts…'):
                with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda t:refresh_week(data,t),pending))
    rows=[]
    for offset in range(0,len(symbols),4):
        columns=st.columns(min(4,len(symbols)-offset))
        for col,ticker in zip(columns,symbols[offset:offset+4]):
            market=load(data,ticker);p=market.get('profile',{});bars=frame(market)
            verdict='Research not run';note='Open Investigate to build the dated investment case.';report_date='—'
            try:
                report=load_latest(data,ticker);brief=decision_brief(report)
                verdict=brief['verdict'];note=brief['summary'];report_date=report['as_of'][:16]
            except (OSError,ValueError,KeyError):pass
            change=p.get('regularMarketChangePercent');color='#29e5af' if (change or 0)>=0 else '#ff617d'
            month=(bars.Close.iloc[-1]/bars.Close.iloc[-22]-1)*100 if len(bars)>21 else None
            with col:
                st.markdown(f'<div class="ad-watch-card"><div class="ad-kicker">{escape(ticker)}</div><h3>{escape(p.get("shortName") or ticker)}</h3><strong>{fmt(p.get("regularMarketPrice"))} <small style="color:{color}">{f"{change:+.2f}%" if change is not None else "—"}</small></strong><p>{escape(p.get("sector") or "Awaiting company snapshot")}</p></div>',unsafe_allow_html=True)
                if not bars.empty:
                    chart,move=watch_chart(bars,period,snapshot=market)
                    if chart is not None:
                        st.markdown(f'<div class="ad-period-change" style="color:{move["color"]};font: bold 13px Menlo,monospace;margin-top:8px">{move["percent"]:+.2f}% · {move["change"]:+.2f} · {period}</div>',unsafe_allow_html=True)
                        st.plotly_chart(chart,use_container_width=True,key='watch_chart_'+ticker,config={'displayModeBar':False,'scrollZoom':False})
                        st.caption(f'{"15-minute prices" if move["intraday"] else "Daily closes"} · {move["start"]:%b %d}–{move["end"]:%b %d, %Y}')
                    else:st.caption('Price history unavailable.')
                st.caption('Quote · '+clock(p.get('regularMarketTime')))
                st.markdown(f'<div class="ad-watch-summary"><b>{escape(verdict)}</b><p>{escape(note)}</p></div>',unsafe_allow_html=True)
                from advisor.investigator.jobs import active_job
                running=active_job(data,ticker)
                if running:st.caption('⏳ Investigation running · Open the Investigate tab for progress')
                if st.button('Investigating '+ticker+'…' if running else 'Investigate '+ticker,key='watch_open_'+ticker,use_container_width=True,disabled=bool(running)):
                    st.session_state['ad_requested_page']='INT';st.session_state['ad_symbol']=ticker;st.session_state['investigator_ticker']=ticker;st.session_state['ad_market_requested']=ticker;st.session_state['ad_investigate_requested']=ticker;st.rerun()
            rows.append({'Ticker':ticker,'Company':p.get('shortName',ticker),'Price':p.get('regularMarketPrice'),'Day %':change,'1M %':month,'Market cap':fmt(p.get('marketCap'),'money'),'Forward P/E':p.get('forwardPE'),'Research':verdict,'Research as of':report_date,'Quote as of':clock(p.get('regularMarketTime'))})
    st.markdown('### Watchlist snapshot')
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True,column_config={'Day %':st.column_config.NumberColumn(format='%+.2f%%'),'1M %':st.column_config.NumberColumn(format='%+.2f%%'),'Price':st.column_config.NumberColumn(format='%.2f')})
    st.caption('Market changes and sparklines are descriptive. A watchlist entry does not initiate a trade or an automatic investment recommendation.')
