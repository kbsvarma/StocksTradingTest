import json
from advisor.investigator.archive import recent_reports, generated_label
from advisor.investigator.engine import digest


def saved(data,ticker,run,stamp,state='complete'):
    root=data/'intelligence/investigations'/ticker/run
    root.mkdir(parents=True)
    report={'ticker':ticker,'run_id':run,'as_of':stamp,'summary':f'Saved {run}'}
    report['snapshot_hash']=digest(report)
    (root/'report.json').write_text(json.dumps(report))
    (root/'status.json').write_text(json.dumps({'ticker':ticker,'run_id':run,
        'updated_at':stamp,'state':state}))


def test_archive_returns_five_newest_saved_runs_with_dates_and_workspace_isolation(tmp_path):
    for i in range(1,8):
        saved(tmp_path,'MSFT' if i%2 else 'NVDA',f'run{i}',f'2026-09-0{i}T18:00:00+00:00')
    saved(tmp_path,'ORCL','failed','2026-09-08T18:00:00+00:00',state='failed')
    rows=recent_reports(tmp_path)
    assert [r['run_id'] for r in rows]==['run7','run6','run5','run4','run3']
    assert rows[0]['label']=='Monday, 07 Sep 2026 · 02:00 PM EDT'
    assert recent_reports(tmp_path/'other_workspace')==[]
    assert generated_label('2026-01-05T18:00:00Z').endswith('01:00 PM EST')


def test_archive_click_opens_exact_historical_report_without_new_investigation(tmp_path):
    from streamlit.testing.v1 import AppTest
    saved(tmp_path,'MSFT','older','2026-09-04T18:00:00+00:00')
    saved(tmp_path,'MSFT','newer','2026-09-07T18:00:00+00:00')
    app=AppTest.from_string('''
import streamlit as st
from advisor.investigator.workspace import render_archive
from advisor.investigator.engine import load_run
render_archive(st.session_state['data'])
chosen=st.session_state.get('investigation_archive_selection')
if chosen:
    st.write(load_run(st.session_state['data'],chosen['ticker'],chosen['run_id'])['summary'])
''')
    app.session_state['data']=str(tmp_path)
    app.run()
    app.button(key='archive_MSFT_older').click().run()
    assert not app.exception
    assert app.session_state['investigator_ticker']=='MSFT'
    assert app.session_state['investigation_archive_selection']['run_id']=='older'
    assert any('Saved older' in x.value for x in app.markdown)
    assert 'ad_investigate_requested' not in app.session_state
