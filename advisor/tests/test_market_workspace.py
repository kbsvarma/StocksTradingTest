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
    fig=figure(stock)
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
    fig=figure(snapshot,period='1M')
    assert 18<=len(fig.data[0].x)<=24
    assert min(fig.data[0].close)>470
    ma200=next(t for t in fig.data if t.name=='MA 200')
    assert all(pd.notna(v) for v in ma200.y)


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
