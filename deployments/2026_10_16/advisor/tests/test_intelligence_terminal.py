"""Real Streamlit execution across workspaces, not string-only UI checks."""
import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from advisor.intelligence.terminal_data import parse_command


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr('advisor.market_view.refresh',lambda *args,**kwargs:{})
    data=tmp_path/'data'; (data/'research').mkdir(parents=True)
    (data/'research'/'candidates_latest.json').write_text(json.dumps({'as_of':'2026-09-04T20:30:00Z',
        'slate':[{'ticker':'TEST','buckets':['tactical_long'],'detail':{'sector':'Technology','px':100,'score':1.2}},
                 {'ticker':'OTHER','buckets':[],'detail':{'sector':'Industrials','px':50}}]}))
    monkeypatch.setenv('ADVISOR_DATA_DIR',str(data))
    monkeypatch.setenv('ADVISOR_AUTH_MODE','local')
    monkeypatch.delenv('ADVISOR_PORTAL_TOKEN',raising=False)
    return AppTest.from_file(str(Path(__file__).resolve().parents[1]/'terminal.py')).run(timeout=20)


@pytest.mark.parametrize('page',['01  DESK','02  CALL BOOK','03  SECURITY','04  EVIDENCE','05  CATALYSTS','06  PERFORMANCE','07  RESEARCH LAB','08  OPERATIONS'])
def test_workspaces_render_missing_data_without_exceptions(app,page):
    assert not app.exception
    app.radio(key='ad_nav').set_value(page).run(timeout=20)
    assert not app.exception


def test_scenario_workbench_recalculates_without_writing_calls(app):
    app.radio(key='ad_nav').set_value('07  RESEARCH LAB').run()
    app.selectbox[0].select('Scenario sensitivity').run()
    assert not app.exception
    app.slider[0].set_value(90).run()
    assert any('exceed' in e.value for e in app.error)


def test_command_grammar_is_bounded_and_known_symbol_only():
    assert parse_command('test des',{'TEST'}) == ('SEC','TEST',None)
    assert parse_command('TEST EVID',{'TEST'}) == ('EVID','TEST',None)
    assert parse_command('OPS',{'TEST'}) == ('OPS',None,None)
    assert parse_command('../../secrets DES',{'TEST'})[2]
    assert parse_command('TEST BUY',{'TEST'})[2]


def test_command_switches_security_after_picker_already_exists(app):
    def command(text):
        next(x for x in app.text_input if x.label=='COMMAND').set_value(text)
        next(b for b in app.button if b.label=='GO ↵').click().run()
    command('TEST DES')
    assert app.selectbox(key='security_picker').value=='TEST'
    command('OTHER DES')
    assert app.selectbox(key='security_picker').value=='OTHER' and not app.exception


def test_thesis_builder_saves_an_explicitly_blocked_hypothesis(app):
    app.radio(key='ad_nav').set_value('07  RESEARCH LAB').run()
    app.selectbox[0].select('Thesis builder').run()
    app.text_area(key='thesis_what_changed').set_value('Management increased its demand forecast; verify the release.')
    next(b for b in app.button if b.label=='Save research hypothesis').click().run()
    assert not app.exception and any('Hypothesis saved' in x.value for x in app.success)


def test_save_workspace_is_functional_and_has_valid_toast(app):
    next(b for b in app.button if b.label=='Save workspace').click().run()
    assert not app.exception


def test_on_demand_investigator_accepts_an_uncovered_ticker_without_fetching(app,monkeypatch):
    from advisor.investigator import jobs
    def unexpected(*args,**kwargs):raise AssertionError('Rendering must not launch research')
    monkeypatch.setattr('advisor.investigator.workspace.start',unexpected)
    next(x for x in app.text_input if x.label=='COMMAND').set_value('NVDA INT')
    next(b for b in app.button if b.label=='GO ↵').click().run()
    assert not app.exception
    assert app.session_state['investigator_ticker']=='NVDA'
    assert app.radio(key='ad_nav').value=='09  INVESTIGATE'
    assert not any('default value' in w.value for w in app.warning)


def test_refresh_preserves_investigator_workspace(app):
    app.radio(key='ad_nav').set_value('09  INVESTIGATE').run()
    next(b for b in app.button if b.label=='↻ Refresh data').click().run()
    assert not app.exception and app.radio(key='ad_nav').value=='09  INVESTIGATE'


@pytest.mark.parametrize('query',['Nividia','Nvidia','Nvda','NVDIA'])
def test_company_search_launches_one_full_investigation(app,monkeypatch,query):
    from advisor.investigator import jobs
    calls=[]
    def launch(*args,**kwargs):
        calls.append((args[1],kwargs))
        return {'ticker':'NVDA','run_id':'testjob'}
    monkeypatch.setattr('advisor.investigator.workspace.start',launch)
    app.text_input(key='ad_stock_search').set_value(query)
    next(b for b in app.button if b.label=='Investigate →').click().run()
    assert not app.exception
    assert app.session_state['investigator_ticker']=='NVDA'
    assert app.radio(key='ad_nav').value=='09  INVESTIGATE'
    assert calls==[('NVDA',{'deep':True})]
    app.run()
    assert len(calls)==1


def test_watchlist_defaults_render_and_empty_selection_persists(app):
    app.radio(key='ad_nav').set_value('10  WATCHLIST').run()
    assert not app.exception
    labels=[b.label for b in app.button]
    assert all('Investigate '+t in labels for t in ('NVDA','AAPL','MSFT','GOOGL'))
    next(m for m in app.multiselect if m.label=='Remove from watchlist').set_value(['NVDA','AAPL','MSFT','GOOGL']).run()
    next(b for b in app.button if b.label=='Remove selected').click().run()
    assert not app.exception and any('empty' in i.value for i in app.info)
    app.radio(key='ad_nav').set_value('01  DESK').run()
    app.radio(key='ad_nav').set_value('10  WATCHLIST').run()
    assert any('empty' in i.value for i in app.info)


def test_active_job_disables_run_buttons_and_shows_animated_progress(app,monkeypatch):
    job={'ticker':'NVDA','run_id':'activejob','status':{'state':'running','stage':'research','detail':'Checking competing explanations'}}
    monkeypatch.setattr('advisor.investigator.jobs.active_job',lambda *a:job)
    monkeypatch.setattr('advisor.investigator.workspace.active_job',lambda *a:job)
    monkeypatch.setattr('advisor.investigator.workspace.read_status',lambda *a:job['status'])
    app.session_state['investigator_ticker']='NVDA'
    app.session_state['investigation_job']=job
    app.radio(key='ad_nav').set_value('09  INVESTIGATE').run()
    assert not app.exception
    buttons=[b for b in app.button if b.label=='Investigating…']
    assert len(buttons)==2 and all(b.disabled for b in buttons)
    assert any('ad-research-spinner' in m.value and 'INVESTIGATING NVDA' in m.value for m in app.markdown)
