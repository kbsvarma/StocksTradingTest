import json
import pandas as pd
import pytest
from advisor.market_data import refresh,load,save_symbols,watch_symbols
from advisor.market_view import figure
from advisor.intelligence.store import CallStore
from advisor.intelligence.access import Principal

class Market:
    info={'regularMarketPrice':120,'regularMarketTime':1788566400,'shortName':'Test','currency':'USD'}
    def history(self,**kwargs):
        assert kwargs['period']=='5y' and kwargs['auto_adjust'] is True
        return pd.DataFrame({'Open':[99,100,110],'High':[101,111,121],'Low':[98,99,109],'Close':[100,110,120],'Volume':[10,20,30]},index=pd.date_range('2026-09-01',periods=3,name='Date',tz='UTC'))

def test_market_fetch_requests_five_years_and_preserves_good_data_on_failure(tmp_path):
    good=refresh(tmp_path,'TEST',factory=lambda _:Market())
    class Failed:
        def history(self,**kw):raise TimeoutError()
        @property
        def info(self):raise TimeoutError()
    failed=refresh(tmp_path,'TEST',factory=lambda _:Failed())
    assert failed['bars']==good['bars'] and failed['profile']==good['profile']
    assert failed['history_as_of']==good['history_as_of'] and len(failed['errors'])==2
    assert load(tmp_path,'TEST')['bars']

def test_chart_offers_real_ohlc_volume_ranges_and_common_start_comparison(tmp_path):
    stock=refresh(tmp_path,'TEST',factory=lambda _:Market())
    fig=figure(stock,style='Candles',averages=True,volume=True)
    assert fig.data[0].type=='candlestick'
    assert fig.data[-1].type=='bar'
    assert fig.layout.xaxis.matches is None and fig.layout.xaxis2.matches=='x'
    peer=dict(stock,ticker='SPY');compare=figure(stock,compare=peer)
    assert compare.data[0].y[0]==0 and compare.data[1].y[0]==0
    assert compare.data[0].name=='TEST' and compare.data[1].name=='SPY'

def test_watchlist_persists_empty_and_user_isolation(tmp_path):
    path=tmp_path/'calls.sqlite'
    with CallStore(path,Principal('one','model','analyst')) as store:
        assert watch_symbols(store)==['NVDA','AAPL','MSFT','GOOGL']
        save_symbols(store,['NVDA','AAPL','NVDA'])
    with CallStore(path,Principal('one','model','analyst')) as store:
        assert watch_symbols(store)==['NVDA','AAPL'];save_symbols(store,[])
        assert watch_symbols(store)==[]
    with CallStore(path,Principal('two','model','analyst')) as store:
        assert watch_symbols(store)==['NVDA','AAPL','MSFT','GOOGL']
    with CallStore(path,Principal('one','other','analyst')) as store:
        assert watch_symbols(store)==['NVDA','AAPL','MSFT','GOOGL']


def test_short_range_rescales_data_but_keeps_full_history_moving_average():
    dates=pd.bdate_range('2025-01-01',periods=400,tz='UTC')
    snapshot={'ticker':'TEST','bars':[{'Date':d.isoformat(),'Close':100+i,'Open':100+i,'High':101+i,'Low':99+i,'Volume':100} for i,d in enumerate(dates)]}
    fig=figure(snapshot,period='1M',style='Candles',averages=True)
    assert 18<=len(fig.data[0].x)<=24
    assert min(fig.data[0].close)>470
    ma200=next(t for t in fig.data if t.name=='MA 200')
    assert all(pd.notna(v) for v in ma200.y)


def test_investigate_defaults_to_last_five_sessions_with_long_ranges_available():
    dates=pd.bdate_range('2025-01-01',periods=400,tz='UTC')
    snapshot={'ticker':'TEST','bars':[{'Date':d.isoformat(),'Close':100+i,'Open':100+i,'High':101+i,'Low':99+i,'Volume':100} for i,d in enumerate(dates)]}
    from advisor.market_view import selected_history
    fig=figure(snapshot,averages=True)
    assert list(selected_history(snapshot,'1W')[0].index)==list(dates[-5:])
    assert len(fig.data[0].x)==5
    assert len(figure(snapshot,period='5Y').data[0].x)==400
    assert all(pd.notna(v) for v in next(t for t in fig.data if t.name=='MA 200').y)


def test_investigate_range_resets_on_new_ticker_and_preserves_same_ticker_choice(tmp_path):
    from streamlit.testing.v1 import AppTest
    from advisor.market_data import refresh
    for ticker in ('TEST','NEXT'):
        refresh(tmp_path,ticker,factory=lambda _:Market())
        from advisor.market_data import refresh_week
        refresh_week(tmp_path,ticker,factory=lambda _:Market())
    app=AppTest.from_string('''
import streamlit as st
from pathlib import Path
from advisor.market_view import render_market
render_market(Path(st.session_state['data']),st.session_state['ticker'])
''')
    app.session_state['data']=str(tmp_path)
    app.session_state['ticker']='TEST'
    app.run()
    assert not app.exception
    assert app.session_state['market_period']=='1W'
    control=app.get('button_group')[0]
    control.set_value('5Y' if hasattr(control,'_is_single_select') else ['5Y']).run()
    assert app.session_state['market_period']=='5Y'
    app.session_state['ticker']='NEXT'
    control=app.get('button_group')[0]
    control.set_value('5Y' if hasattr(control,'_is_single_select') else ['5Y']).run()
    assert not app.exception
    assert app.session_state['market_period']=='1W'


def test_watch_chart_scales_visible_prices_and_reports_period_return():
    from advisor.watchlist_workspace import watch_chart
    bars=pd.DataFrame({'Close':[200+i*.2 for i in range(100)]},index=pd.bdate_range('2026-01-01',periods=100))
    lengths=[]
    for period in ('1W','1M','3M'):
        chart,move=watch_chart(bars,period)
        lengths.append(len(chart.data[0].x))
        assert chart.layout.yaxis.range[0]>190
        assert chart.layout.yaxis.range[0]<min(chart.data[0].y)
        assert chart.layout.yaxis.range[1]>max(chart.data[0].y)
        assert move['percent']==pytest.approx((chart.data[0].y[-1]/chart.data[0].y[0]-1)*100)
    assert lengths[0]<lengths[1]<lengths[2]
    flat=pd.DataFrame({'Close':[230,230]},index=pd.bdate_range('2026-09-01',periods=2))
    chart,move=watch_chart(flat)
    assert chart.layout.yaxis.range[0]<230<chart.layout.yaxis.range[1]
    assert move['percent']==0


def test_week_uses_real_intraday_movement_with_clean_price_scale():
    from advisor.market_view import selected_history
    bars=[]
    for day in pd.bdate_range('2026-08-28',periods=6,tz='America/New_York'):
        for i in range(26):
            price=260-i*.2
            bars.append({'Date':(day+pd.Timedelta(hours=9,minutes=30+15*i)).isoformat(),
                'Close':price,'Open':price+.1,'High':price+.2,'Low':price-.2,'Volume':100})
    dates=pd.bdate_range('2025-01-01',periods=400,tz='UTC')
    snapshot={'ticker':'TEST','bars':[{'Date':d.isoformat(),'Close':190,'Open':190,'High':191,'Low':189,'Volume':100} for d in dates], 'intraday_bars':bars}
    visible,intraday=selected_history(snapshot,'1W')
    assert intraday and len(visible)==131
    assert len(visible.index.normalize().unique())==5
    assert visible.Close.iloc[0]==260.1  # actual opening observation
    assert visible.index[0].strftime('%H:%M')=='09:30'
    assert visible.index[-1].strftime('%H:%M')=='16:00'
    fig=figure(snapshot)
    assert len(fig.data)==1 and fig.data[0].type=='scatter'
    assert fig.data[0].mode=='lines' and fig.data[0].line.color=='#f28b82'
    assert fig.layout.yaxis.range[0]>250
    assert len(fig.layout.xaxis.tickvals)==5
    assert all('00:00' not in label for label in fig.layout.xaxis.ticktext)
    assert not fig.layout.showlegend
    month=figure(snapshot,period='1M')
    assert len(month.data[0].x)>len(fig.data[0].x)
    assert month.layout.yaxis.range[0]>250
    from advisor.market_data import frame
    from advisor.watchlist_workspace import watch_chart
    for period in ('1W','1M'):
        watch,move=watch_chart(frame(snapshot),period,snapshot=snapshot)
        full=figure(snapshot,period=period)
        assert watch.layout.yaxis.range==full.layout.yaxis.range
        assert move['intraday']
        assert len(watch.data[0].x)==len(full.data[0].x)


def test_intraday_cache_retains_real_observations_on_provider_failure(tmp_path):
    from advisor.market_data import refresh_week
    class Intraday:
        def history(self,**kw):
            assert kw['interval']=='15m' and kw['prepost'] is False
            return pd.DataFrame({'Close':[100,102],'Open':[99,101]},index=pd.date_range('2026-09-04 09:30',periods=2,freq='15min',tz='America/New_York',name='Datetime'))
    good=refresh_week(tmp_path,'TEST',factory=lambda _:Intraday())
    class Failed:
        def history(self,**kw):raise TimeoutError()
    bad=refresh_week(tmp_path,'TEST',factory=lambda _:Failed())
    assert good['intraday_bars']==bad['intraday_bars']
    assert good['intraday_as_of']==bad['intraday_as_of']
    assert bad['intraday_error']
