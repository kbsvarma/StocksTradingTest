"""Interactive five-year market context, distinct from investment conclusions."""
from html import escape
from datetime import datetime,timezone
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from advisor.market_data import load,refresh,refresh_week,frame,intraday_due


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

def selected_history(snapshot,period):
    daily=frame(snapshot)
    if daily.empty:return daily,False
    for n in (50,200):daily[f'MA{n}']=daily.Close.rolling(n).mean()
    if period in {'1W','1M'}:
        intraday=frame({'bars':snapshot.get('intraday_bars',[])})
        if not intraday.empty:
            intraday.index=intraday.index.tz_convert('America/New_York')
            days=intraday.index.normalize()
            selected=days.isin(days.unique()[-5:]) if period=='1W' else days>=days[-1]-pd.DateOffset(months=1)
            intraday=intraday.loc[selected].copy()
            # Provider timestamps mark the beginning of each 15-minute bar.
            # Plot its close at the end; include the actual opening observation.
            opened=intraday.index[0]
            opening=intraday.iloc[0].get('Open')
            intraday.index=intraday.index+pd.Timedelta(minutes=15)
            if pd.notna(opening):
                first=pd.DataFrame({'Close':[opening],'Open':[opening],'High':[opening],'Low':[opening]},index=[opened])
                intraday=pd.concat([first,intraday]).sort_index()
            # Daily averages become available only after that day's close.
            settled=daily[['MA50','MA200']].copy()
            settled.index=settled.index.tz_convert('America/New_York').normalize()+pd.Timedelta(hours=16)
            intraday=intraday.join(settled.reindex(intraday.index,method='ffill'))
            return intraday,True
        if period=='1W':return daily.tail(5),False
    last=daily.index[-1]
    cutoff={'1M':last-pd.DateOffset(months=1),'3M':last-pd.DateOffset(months=3),'6M':last-pd.DateOffset(months=6),'YTD':pd.Timestamp(year=last.year,month=1,day=1,tz=last.tz),'1Y':last-pd.DateOffset(years=1)}.get(period,daily.index[0])
    return daily.loc[daily.index>=cutoff],False


def figure(snapshot,*,style='Line',averages=False,compare=None,period='1W',volume=False):
    f,intraday=selected_history(snapshot,period)
    if f.empty:return None
    ticker=snapshot['ticker'];close=f.Close
    fig=make_subplots(rows=2 if volume else 1,cols=1,shared_xaxes=True,
        vertical_spacing=.025,row_heights=[.82,.18] if volume else [1])
    # Trading observations share equal spacing: nights and exchange holidays
    # do not create blank gaps or repeated midnight/noon labels.
    x=list(range(len(f)))
    stamps=[d.tz_convert('America/New_York').strftime('%a, %b %d · %I:%M %p %Z') if intraday else d.strftime('%a, %b %d, %Y') for d in f.index]
    bounds=close.tolist();compared=False
    if compare:
        b,_=selected_history(compare,period)
        aligned=f[['Close']].join(b[['Close']].rename(columns={'Close':'Benchmark'}),how='inner').dropna()
        if not aligned.empty:
            compared=True;bounds=[]
            for col,name,color in [('Close',ticker,'#81c995'),('Benchmark',compare['ticker'],'#a8c7fa')]:
                y=(aligned[col]/aligned[col].iloc[0]-1)*100
                bounds+=y.tolist()
                fig.add_trace(go.Scatter(x=[f.index.get_loc(d) for d in aligned.index],y=y,mode='lines',name=name,line=dict(color=color,width=2),customdata=[stamps[f.index.get_loc(d)] for d in aligned.index],hovertemplate='%{y:+.2f}% · %{customdata}<extra>%{fullData.name}</extra>'),row=1,col=1)
    if not compared:
        color='#81c995' if close.iloc[-1]>=close.iloc[0] else '#f28b82'
        if style=='Candles' and {'Open','High','Low'}<=set(f):
            fig.add_trace(go.Candlestick(x=x,open=f.Open,high=f.High,low=f.Low,close=close,name=ticker,increasing_line_color='#81c995',decreasing_line_color='#f28b82'),row=1,col=1)
            bounds+=f.High.dropna().tolist()+f.Low.dropna().tolist()
        else:
            fig.add_trace(go.Scatter(x=x,y=close,mode='lines',name=ticker,line=dict(color=color,width=2.5),fill='tozeroy',fillcolor='rgba(129,201,149,0.10)' if color=='#81c995' else 'rgba(242,139,130,0.10)',customdata=stamps,hovertemplate='%{y:,.2f} '+str(snapshot.get('profile',{}).get('currency') or '')+' · %{customdata}<extra></extra>'),row=1,col=1)
        if averages:
            for n,color in [(50,'#ffbe38'),(200,'#bc8aff')]:
                bounds+=f[f'MA{n}'].dropna().tolist()
                fig.add_trace(go.Scatter(x=x,y=f[f'MA{n}'],mode='lines',name=f'MA {n}',line=dict(color=color,width=1)),row=1,col=1)
    lo,hi=min(bounds),max(bounds)
    pad=max((hi-lo)*.12,abs(hi)*.002,.01)
    if volume and 'Volume' in f:
        fig.add_trace(go.Bar(x=x,y=f.Volume,name='Volume',marker_color=['#38664a' if v>=0 else '#773f40' for v in close.diff().fillna(0)],showlegend=False),row=2,col=1)
    if intraday and period=='1W':
        ticks=[i for i,d in enumerate(f.index) if i==0 or d.date()!=f.index[i-1].date()]
    else:
        ticks=sorted(set(round(i*(len(f)-1)/min(4,len(f)-1)) for i in range(min(5,len(f))))) if len(f)>1 else [0]
    fig.update_layout(template='plotly_dark',height=410 if not volume else 480,
        paper_bgcolor='#0b1015',plot_bgcolor='#0b1015',margin=dict(l=8,r=28,t=30 if averages or compared else 12,b=15),
        font=dict(family='Arial',size=13,color='#c4c7c5'),hovermode='x',dragmode='pan',
        showlegend=averages or compared,legend=dict(orientation='h',y=1.13,x=0),
        uirevision=ticker+period+style+str((averages,volume,bool(compare))),
        xaxis=dict(rangeslider=dict(visible=False)),hoverlabel=dict(bgcolor='#242a31',font_size=14))
    fig.update_xaxes(type='linear',range=[0,len(f)-1 if len(f)>1 else 1],tickmode='array',tickvals=ticks,
        ticktext=[f.index[i].strftime('%b %d') if period in {'1W','1M','3M'} else f.index[i].strftime('%b %Y') for i in ticks],
        ticklabeloverflow='allow',showgrid=False,zeroline=False,showspikes=True,spikemode='across',spikesnap='cursor',spikecolor='#73777c',spikethickness=1)
    fig.update_yaxes(range=[lo-pad,hi+pad],side='left',fixedrange=False,nticks=5,tickformat=',.2~f',
        ticksuffix='%' if compared else '',gridcolor='#30363d',zeroline=False,row=1,col=1)
    if volume:
        fig.update_xaxes(matches=None,row=1,col=1)
        fig.update_xaxes(matches='x',row=2,col=1)
        fig.update_yaxes(nticks=2,gridcolor='#20262e',row=2,col=1)
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
    if st.session_state.get('market_period_ticker')!=ticker:
        st.session_state['market_period']='1W'
        st.session_state['market_period_ticker']=ticker
    # Migrate existing sessions away from the old technical-chart defaults.
    if st.session_state.get('market_chart_design')!=2:
        st.session_state.update(market_style='Line',market_averages=False,market_volume=False,market_chart_design=2)
    period=st.segmented_control('Chart range',['1W','1M','6M','YTD','1Y','5Y'],key='market_period',label_visibility='collapsed') or '1W'
    if period in {'1W','1M'}:
        if trigger==ticker or intraday_due(snapshot):
            with st.spinner('Loading detailed price history…'):snapshot=refresh_week(data,ticker)
    with st.expander('Chart tools · candles, comparison & indicators'):
        a,b=st.columns(2)
        with a:style=st.selectbox('CHART STYLE',['Line','Candles'],key='market_style')
        with b:compare=st.selectbox('COMPARE RETURNS',['None','SPY','QQQ'],key='market_compare')
        a,b=st.columns(2)
        with a:averages=st.checkbox('50 & 200-day averages',key='market_averages',disabled=compare!='None')
        with b:volume=st.checkbox('Volume',key='market_volume')
    benchmark=load(data,compare) if compare!='None' else None
    if benchmark is not None:
        if not benchmark.get('bars'):benchmark=refresh(data,compare)
        if period in {'1W','1M'} and intraday_due(benchmark):benchmark=refresh_week(data,compare)
    visible,intraday=selected_history(snapshot,period)
    if not visible.empty:
        delta=float(visible.Close.iloc[-1]-visible.Close.iloc[0]);pct=delta/float(visible.Close.iloc[0])*100
        tone='#81c995' if delta>=0 else '#f28b82'
        window={'1W':'past 5 trading days','1M':'past month','6M':'past 6 months','YTD':'year to date','1Y':'past year','5Y':'shown history'}.get(period,period)
        st.markdown(f'<div style="color:{tone};font-size:22px;font-weight:600;margin:8px 0 14px"><span style="background:{tone}20;padding:6px 12px;border-radius:6px">{pct:+.2f}%</span> &nbsp; {delta:+,.2f} {escape(p.get("currency") or "")} {window}</div>',unsafe_allow_html=True)
    chart=figure(snapshot,style=style,averages=averages,compare=benchmark,period=period,volume=volume)
    st.plotly_chart(chart,use_container_width=True,key='investigation_market_chart_'+ticker,config={'displayModeBar':False,'displaylogo':False,'scrollZoom':False,'responsive':True})
    if period in {'1W','1M'} and not intraday:st.caption('Intraday history unavailable · showing daily closes, not intraday movement. Use Refresh market snapshot to retry.')
    stamp=visible.index[-1].tz_convert('America/New_York').strftime('%a %d %b %Y · %I:%M %p %Z' if intraday else '%a %d %b %Y') if not visible.empty else 'unavailable'
    st.caption(f'{"15-minute prices · regular session" if intraday else "Adjusted daily closes"} · Through {stamp} · Hover for price and time; drag to pan, double-click to reset.')
    if snapshot.get('intraday_error') and period in {'1W','1M'}:st.caption(snapshot['intraday_error']+' · Any retained observations keep their original dates.')
    fields=[('Market cap','marketCap','money'),('Forward P/E','forwardPE','number'),('Trailing P/E','trailingPE','number'),('EPS','trailingEps','number'),('52-week low','fiftyTwoWeekLow','number'),('52-week high','fiftyTwoWeekHigh','number'),('Open','regularMarketOpen','number'),('Day low','regularMarketDayLow','number'),('Day high','regularMarketDayHigh','number'),('Volume','regularMarketVolume','money')]
    st.markdown('<div class="ad-facts-grid">'+''.join(f'<div><span>{name}</span><b>{fmt(p.get(key),kind)}</b></div>' for name,key,kind in fields)+'</div>',unsafe_allow_html=True)
    st.caption('Company metrics are provider snapshots, retrieved '+snapshot.get('profile_retrieved_at','unavailable')[:19]+'. Their fiscal periods may differ; see dated research evidence for accounting comparisons.')
    if snapshot.get('errors'):st.warning('Refresh incomplete; retained dated data: '+'; '.join(snapshot['errors']))
    if st.button('Refresh market snapshot',key='refresh_market_'+ticker):st.session_state['ad_market_requested']=ticker;st.rerun()
