"""Per-user persistent watchlists with dated market and research summaries."""
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
import pandas as pd
import streamlit as st
from advisor.intelligence.store import CallStore
from advisor.market_data import load,refresh,frame,watch_symbols,save_symbols
from advisor.market_view import fmt,clock
from advisor.investigator.search import resolve,AmbiguousCompany
from advisor.investigator.engine import load_latest
from advisor.investigator.presentation import decision_brief


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
                if not bars.empty:st.line_chart(bars[['Close']].tail(63),height=120,use_container_width=True,color=color)
                st.caption('Quote · '+clock(p.get('regularMarketTime')))
                st.markdown(f'<div class="ad-watch-summary"><b>{escape(verdict)}</b><p>{escape(note)}</p></div>',unsafe_allow_html=True)
                if st.button('Investigate '+ticker,key='watch_open_'+ticker,use_container_width=True):
                    st.session_state['ad_requested_page']='INT';st.session_state['ad_symbol']=ticker;st.session_state['investigator_ticker']=ticker;st.session_state['ad_market_requested']=ticker;st.rerun()
            rows.append({'Ticker':ticker,'Company':p.get('shortName',ticker),'Price':p.get('regularMarketPrice'),'Day %':change,'1M %':month,'Market cap':fmt(p.get('marketCap'),'money'),'Forward P/E':p.get('forwardPE'),'Research':verdict,'Research as of':report_date,'Quote as of':clock(p.get('regularMarketTime'))})
    st.markdown('### Watchlist snapshot')
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True,column_config={'Day %':st.column_config.NumberColumn(format='%+.2f%%'),'1M %':st.column_config.NumberColumn(format='%+.2f%%'),'Price':st.column_config.NumberColumn(format='%.2f')})
    st.caption('Market changes and sparklines are descriptive. A watchlist entry does not initiate a trade or an automatic investment recommendation.')
