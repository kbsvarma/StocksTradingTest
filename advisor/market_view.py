"""Interactive five-year market context, distinct from investment conclusions."""
from html import escape
from datetime import datetime,timezone
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from advisor.market_data import load,refresh,frame


def fmt(v,kind='number'):
    if v is None:return '—'
    if kind=='money':
        for size,suffix in ((1e12,'T'),(1e9,'B'),(1e6,'M')):
            if abs(v)>=size:return f'{v/size:,.2f}{suffix}'
    return f'{v:,.2f}'

def clock(v):
    if not v:return 'unavailable'
    try:return datetime.fromtimestamp(v,timezone.utc).strftime('%d %b %Y %H:%M UTC')
    except (ValueError,TypeError,OSError):return str(v)

def figure(snapshot,*,style='Candles',averages=True,compare=None,period='5Y'):
    f=frame(snapshot)
    if f.empty:return None
    for n in (50,200):f[f'MA{n}']=f.Close.rolling(n).mean()
    last=f.index[-1]
    cutoff={'1M':last-pd.DateOffset(months=1),'6M':last-pd.DateOffset(months=6),'YTD':pd.Timestamp(year=last.year,month=1,day=1,tz=last.tz),'1Y':last-pd.DateOffset(years=1)}.get(period,f.index[0])
    f=f.loc[f.index>=cutoff]
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.025,row_heights=[.82,.18])
    close=f['Close'];ticker=snapshot['ticker']
    if compare:
        b=frame(compare)
        if not b.empty:
            aligned=f[['Close']].join(b[['Close']].rename(columns={'Close':'Benchmark'}),how='inner').dropna()
            if not aligned.empty:
                for col,name,color in [('Close',ticker,'#24e0b0'),('Benchmark',compare['ticker'],'#b990ff')]:
                    fig.add_trace(go.Scatter(x=aligned.index,y=(aligned[col]/aligned[col].iloc[0]-1)*100,name=name,line=dict(color=color,width=2)),row=1,col=1)
                fig.update_yaxes(title_text='Return from common start (%)',row=1,col=1)
    if not fig.data:
        if style=='Candles' and {'Open','High','Low'}<=set(f):
            fig.add_trace(go.Candlestick(x=f.index,open=f.Open,high=f.High,low=f.Low,close=close,name=ticker,increasing_line_color='#33d17a',decreasing_line_color='#ff5c57'),row=1,col=1)
        else:fig.add_trace(go.Scatter(x=f.index,y=close,name=ticker,line=dict(color='#33d17a',width=2)),row=1,col=1)
        if averages:
            for n,color in [(50,'#ffbe38'),(200,'#bc8aff')]:fig.add_trace(go.Scatter(x=f.index,y=f[f'MA{n}'],name=f'MA {n}',line=dict(color=color,width=1)),row=1,col=1)
    if 'Volume' in f:fig.add_trace(go.Bar(x=f.index,y=f.Volume,name='Volume',marker_color=['#176954' if x>=0 else '#773443' for x in close.diff().fillna(0)],showlegend=False),row=2,col=1)
    fig.update_layout(template='plotly_dark',height=470,paper_bgcolor='#000000',plot_bgcolor='#000000',margin=dict(l=12,r=15,t=65,b=10),font=dict(family='Arial',size=12,color='#e8e6e3'),hovermode='x unified',dragmode='pan',legend=dict(orientation='h',y=1.12,x=0),uirevision=ticker+period+str(bool(compare)),xaxis=dict(rangeslider=dict(visible=False)),yaxis=dict(side='right',fixedrange=False),yaxis2=dict(side='right',fixedrange=False))
    # The price axis must own the range; volume follows it. Otherwise the
    # subplot factory's reverse match can undo range-selector clicks.
    fig.update_xaxes(matches=None,row=1,col=1)
    fig.update_xaxes(matches='x',row=2,col=1)
    fig.update_xaxes(showgrid=True,gridcolor='#19283a',rangebreaks=[dict(bounds=['sat','mon'])]);fig.update_yaxes(gridcolor='#19283a')
    return fig


def render_market(data,ticker):
    trigger=st.session_state.pop('ad_market_requested',None)
    snapshot=load(data,ticker)
    if trigger==ticker:
        with st.spinner('Loading five years of market history…'):snapshot=refresh(data,ticker)
    if not snapshot.get('bars'):
        st.info('Load the market snapshot to see five years of interactive price history.')
        if st.button('Load chart & market snapshot',key='load_market_'+ticker):
            st.session_state['ad_market_requested']=ticker;st.rerun()
        return
    p=snapshot.get('profile',{});change=p.get('regularMarketChangePercent');color='#33d17a' if (change or 0)>=0 else '#ff5c57'
    st.markdown(f'<div class="ad-quote"><div><small>{escape(p.get("currency") or "Price")} · {escape(p.get("exchange") or "REFERENCE")}</small><strong>{fmt(p.get("regularMarketPrice"))} <span style="color:{color}">{change:+.2f}%</span></strong><small>Market quote · {clock(p.get("regularMarketTime"))}</small></div><div><small>AFTER HOURS</small><strong>{fmt(p.get("postMarketPrice"))}</strong><small>{clock(p.get("postMarketTime"))}</small></div></div>' if change is not None else f'<div class="ad-quote"><strong>{fmt(p.get("regularMarketPrice"))}</strong><small>{clock(p.get("regularMarketTime"))}</small></div>',unsafe_allow_html=True)
    a,b,c=st.columns([2,2,2],vertical_alignment='bottom')
    with a:style=st.selectbox('CHART STYLE',['Candles','Line'],key='market_style',disabled=st.session_state.get('market_compare','None')!='None')
    with b:compare=st.selectbox('COMPARE RETURNS',['None','SPY','QQQ'],key='market_compare')
    with c:averages=st.checkbox('50 & 200-day averages',value=True,disabled=compare!='None')
    if compare!='None':st.caption('Comparison uses rebased lines. Set Compare returns to None to use price candles and moving averages.')
    benchmark=load(data,compare) if compare!='None' else None
    if compare!='None' and not benchmark.get('bars'):
        with st.spinner('Loading comparison history…'):benchmark=refresh(data,compare)
    if compare!='None' and not benchmark.get('bars'):st.warning('Comparison data is unavailable; showing the stock price chart only.')
    period=st.segmented_control('Chart range',['1M','6M','YTD','1Y','5Y'],default='5Y',key='market_period',label_visibility='collapsed') or '5Y'
    chart=figure(snapshot,style=style,averages=averages,compare=benchmark,period=period)
    st.plotly_chart(chart,use_container_width=True,key='investigation_market_chart_'+ticker,config={'displayModeBar':True,'displaylogo':False,'scrollZoom':True,'responsive':True,'modeBarButtonsToRemove':['lasso2d','select2d']})
    st.caption(f'Adjusted daily prices · {snapshot.get("history_as_of","")[:10]} last bar · {len(snapshot["bars"]):,} sessions. Drag to pan, wheel/pinch to zoom, select a range, or double-click to reset. Comparison returns start at the first common session in the selected range; zooming does not change that base.')
    fields=[('Market cap','marketCap','money'),('Forward P/E','forwardPE','number'),('Trailing P/E','trailingPE','number'),('EPS','trailingEps','number'),('52-week low','fiftyTwoWeekLow','number'),('52-week high','fiftyTwoWeekHigh','number'),('Open','regularMarketOpen','number'),('Day low','regularMarketDayLow','number'),('Day high','regularMarketDayHigh','number'),('Volume','regularMarketVolume','money')]
    st.markdown('<div class="ad-facts-grid">'+''.join(f'<div><span>{name}</span><b>{fmt(p.get(key),kind)}</b></div>' for name,key,kind in fields)+'</div>',unsafe_allow_html=True)
    st.caption('Company metrics are provider snapshots, retrieved '+snapshot.get('profile_retrieved_at','unavailable')[:19]+'. Their fiscal periods may differ; see dated research evidence for accounting comparisons.')
    if snapshot.get('errors'):st.warning('Refresh incomplete; retained dated data: '+'; '.join(snapshot['errors']))
    if st.button('Refresh market snapshot',key='refresh_market_'+ticker):st.session_state['ad_market_requested']=ticker;st.rerun()
