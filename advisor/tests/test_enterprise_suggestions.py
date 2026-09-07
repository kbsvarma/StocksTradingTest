"""Failure-path and integrity acceptance tests for the suggestion lifecycle."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from advisor.suggestion_policy import ET, assess_pick, last_complete_session, lifecycle, release_freshness
from advisor.research import generators, picks, pick_tracker, suggestion_store as store
from advisor.research.entry_simulation import simulate
from advisor.tests.test_picks_scoring import env, _slate, _entry


def test_opposition_and_context_never_increase_support():
    base = {'tactical_short': {'rank_pct': .95, 'direction': 'short'}}
    alone = generators.score_candidate(base)
    opposed = generators.score_candidate({**base, 'cheap_quality': {'rank_pct': .9, 'direction': 'long'}})
    contextual = generators.score_candidate({**base, 'squeeze_flag': {'rank_pct': .99, 'direction': None}})
    assert alone['score'] == opposed['score'] == contextual['score']
    assert opposed['conflicted'] and opposed['opposing'] == ['cheap_quality']
    assert contextual['supporting'] == ['tactical_short']


def test_ties_are_order_independent_and_conflicted():
    a = {'tactical_short': {'rank_pct': .95, 'direction': 'short'},
         'cheap_quality': {'rank_pct': .95, 'direction': 'long'}}
    assert generators.score_candidate(a) == generators.score_candidate(dict(reversed(list(a.items()))))
    assert generators.score_candidate(a)['conflicted']


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -.1, 1.01, True, '0.9'])
def test_invalid_percentiles_cannot_lead(bad):
    assert generators.score_candidate({'tactical_long': {'rank_pct': bad, 'direction': 'long'}}) is None


def test_holiday_and_preopen_require_prior_completed_exchange_session():
    # Labor Day and the following preopen must retain Friday's completed bar.
    for stamp in ('2026-09-07T12:00:00-04:00', '2026-09-08T08:00:00-04:00'):
        now = datetime.fromisoformat(stamp)
        assert last_complete_session(now) == '2026-09-04'
        assert release_freshness({'price_bar': '2026-09-04', 'as_of': '2026-09-04T17:00:00-04:00'}, now)['ok']


def test_future_timestamp_and_stale_bar_fail_closed():
    now = datetime.fromisoformat('2026-09-04T12:00:00-04:00')
    assert not release_freshness({'price_bar': '2026-09-02', 'as_of': now.isoformat()}, now)['ok']
    assert not release_freshness({'price_bar': '2026-09-03', 'as_of': (now + timedelta(days=1)).isoformat()}, now)['ok']


@pytest.mark.parametrize('direction,stop,target,px,expected', [
    ('long', 90, 120, 89, 'invalidated'), ('long', 90, 120, 121, 'target_observed'),
    ('short', 120, 90, 121, 'invalidated'), ('short', 120, 90, 89, 'target_observed')])
def test_invalidation_precedes_entry(direction, stop, target, px, expected):
    assert lifecycle({'direction': direction, 'stop': stop, 'target': target,
                      'entry_low': 80, 'entry_high': 130}, px) == expected


def test_missing_quote_and_expiry_cannot_be_entry():
    p = {'direction': 'long', 'stop': 90, 'target': 120, 'entry_low': 99, 'entry_high': 101}
    assert lifecycle(p, float('nan')) == 'quote_unavailable'
    assert lifecycle({**p, 'expires_on': '2000-01-01'}, 100) == 'expired'


def issue(env):
    _slate(env, [_entry('FUND', {'revision_leader': {'rank_pct': .98, 'direction': 'long'}})])
    return picks.build()


def test_stale_rebuild_preserves_last_good_release(env):
    first = issue(env)
    slate = json.loads((env / 'candidates_latest.json').read_text())
    slate['as_of'] = '2000-01-01T08:00:00-05:00'
    (env / 'candidates_latest.json').write_text(json.dumps(slate))
    result = picks.build()
    assert result['error']
    assert json.loads(picks.OUT.read_text())['release_id'] == first['release_id']
    assert json.loads((env / 'suggestion_health.json').read_text())['status'] == 'failed'


def test_mismatched_panel_refused(env):
    issue(env)
    doc = json.loads((env / 'candidates_latest.json').read_text())
    doc['panel_build_id'] = 'different-build'
    (env / 'candidates_latest.json').write_text(json.dumps(doc))
    assert 'mismatch' in picks.build()['error']


def test_same_day_revision_and_retry_identity(env, monkeypatch):
    monkeypatch.setattr(pick_tracker, 'LEDGER', picks.LEDGER)
    first = issue(env)
    again = picks.build()
    assert first['picks'][0]['revision_id'] == again['picks'][0]['revision_id']
    assert again['picks'][0]['change'] == 'unchanged'
    assert len(pick_tracker.effective()) == 1
    _slate(env, [_entry('FUND', {'revision_leader': {'rank_pct': .80, 'direction': 'long'}})])
    changed = picks.build()
    a, b = first['picks'][0], changed['picks'][0]
    assert a['revision_id'] != b['revision_id']
    assert a['episode_id'] == b['episode_id']
    assert b['previous_revision_id'] == a['revision_id']
    assert len(pick_tracker.effective()) == 2
    assert all('entry_low' in r and 'cost' in r for r in pick_tracker.effective().values())


def test_crash_before_commit_never_exposes_or_learns_orphan(env, monkeypatch):
    monkeypatch.setattr(pick_tracker, 'LEDGER', picks.LEDGER)
    first = issue(env)
    _slate(env, [_entry('FUND', {'revision_leader': {'rank_pct': .8, 'direction': 'long'}})])
    atomic = store.atomic_json
    def fail_pointer(path, doc):
        if Path(path) == picks.OUT:
            raise OSError('injected commit failure')
        return atomic(path, doc)
    monkeypatch.setattr(store, 'atomic_json', fail_pointer)
    with pytest.raises(OSError, match='injected'):
        picks.build()
    assert json.loads(picks.OUT.read_text())['release_id'] == first['release_id']
    assert len(pick_tracker.effective()) == 1
    monkeypatch.setattr(store, 'atomic_json', atomic)
    picks.build()
    assert len(pick_tracker.effective()) == 2


def test_journal_mirror_recovery_from_committed_archive(env, monkeypatch):
    monkeypatch.setattr(pick_tracker, 'LEDGER', picks.LEDGER)
    issue(env)
    picks.LEDGER.unlink()
    assert len(pick_tracker.effective()) == 1


def test_archive_tampering_refuses_learning(env, monkeypatch):
    monkeypatch.setattr(pick_tracker, 'LEDGER', picks.LEDGER)
    doc = issue(env)
    archive = env / 'suggestion_releases' / f'{doc["release_id"]}.json'
    payload = json.loads(archive.read_text()); payload['n_picks'] = 100
    archive.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='digest mismatch'):
        pick_tracker.effective()


def test_partial_crash_tail_is_repaired_but_interior_corruption_is_not(tmp_path):
    p = tmp_path / 'rows.jsonl'
    p.write_text('{"id":"a"}\n{"id":')
    store.append_rows(p, [{'id': 'b'}])
    assert store.read_rows(p) == [{'id': 'a'}, {'id': 'b'}]
    p.write_text('{bad}\n{"id":"b"}\n')
    with pytest.raises(ValueError, match='Corrupt'):
        store.append_rows(p, [{'id': 'c'}])


def bars(rows):
    return pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'],
                        index=pd.bdate_range('2026-09-01', periods=len(rows)))


def plan(**kw):
    return dict({'direction': 'long', 'entry_low': 99., 'entry_high': 101.,
                 'stop': 90., 'target': 120., 'cost': {'round_trip_bps': 10}}, **kw)


def test_gap_stop_uses_open_and_deducts_issue_time_cost():
    r = simulate(plan(), bars([[100, 105, 98, 103], [80, 95, 79, 94]]), matured=True)
    assert r['outcome'] == 'stop' and r['exit_px'] == 80
    assert r['return_pct'] == pytest.approx(-20.1)


def test_untriggered_entry_is_not_a_trade():
    r = simulate(plan(), bars([[110, 115, 105, 112]]), matured=True)
    assert r['outcome'] == 'not_entered' and r['return_pct'] is None


def test_intraday_entry_and_exit_order_is_ambiguous():
    r = simulate(plan(), bars([[110, 125, 98, 115]]), matured=True)
    assert r['outcome'] == 'ambiguous' and r['win'] is None


def test_missing_open_or_short_borrow_never_yields_cost_complete_return():
    r = simulate(plan(), bars([[100, 105, 98, 103]]).drop(columns='open'), matured=True)
    assert r['outcome'] == 'simulation_unavailable'
    short = plan(direction='short', stop=120, target=90)
    r = simulate(short, bars([[100, 102, 89, 90]]), matured=True)
    assert r['return_pct'] is None and not r['calibration_eligible']


def test_priority_requires_underwriting_and_earnings_visibility():
    p = {**plan(), 'selection': {'lead_rank_pct': .99, 'n_families': 3}, 'sector': 'Energy',
         'cost_screen': {'verified': True}, 'evidence': {'sources_verified': True}, 'horizon_td': 21}
    r = assess_pick(p, freshness={'ok': True}, today=datetime(2026, 9, 4).date())
    assert r['group'] == 'watchlist'
    assert any('Thesis' in b for b in r['blockers'])
    assert any('earnings' in b for b in r['blockers'])


def test_resolved_stop_comparator_matures_later(tmp_path, monkeypatch):
    ledger = tmp_path / 'picks_ledger.jsonl'
    monkeypatch.setattr(pick_tracker, 'LEDGER', ledger)
    ledger.write_text(json.dumps({'id': 'a', 'type': 'pick', 'ticker': 'A', 'status': 'resolved',
        'price_bar': '2026-09-01', 'horizon_td': 2, 'ref_px': 100,
        'outcome': 'stop', 'direction': 'long', 'unstopped_return_pct': None}) + '\n')
    frame = pd.DataFrame({'A': [100., 90., 110.]}, index=pd.bdate_range('2026-09-01', periods=3))
    monkeypatch.setattr('advisor.research.datastore.load_panel', lambda f: frame)
    pick_tracker.resolve(False)
    assert pick_tracker.effective()['a']['unstopped_return_pct'] == 10
    assert pick_tracker.effective()['a']['outcome'] == 'stop'


def test_panel_pointer_is_pinned_across_nested_readers(tmp_path, monkeypatch):
    from advisor.research import datastore as ds
    monkeypatch.setattr(ds, 'CURRENT', tmp_path / 'CURRENT')
    monkeypatch.setattr(ds, 'BUILDS_DIR', tmp_path)
    for build in ('a', 'b'):
        (tmp_path / build).mkdir()
    ds.CURRENT.write_text('a')
    with ds.pinned_build():
        ds.CURRENT.write_text('b')
        assert ds.current_build_dir().name == 'a'
        with ds.pinned_build():
            assert ds.current_build_dir().name == 'a'
    assert ds.current_build_dir().name == 'b'


def test_reverting_a_same_day_plan_creates_a_new_revision(env, monkeypatch):
    monkeypatch.setattr(pick_tracker, 'LEDGER', picks.LEDGER)
    a = issue(env)
    _slate(env, [_entry('FUND', {'revision_leader': {'rank_pct': .8, 'direction': 'long'}})])
    b = picks.build()
    c = issue(env)
    assert len({r['picks'][0]['revision_id'] for r in (a, b, c)}) == 3
    assert len(pick_tracker.effective()) == 3


def test_reviewed_plan_uses_its_own_horizon_and_levels():
    from advisor.research.suggestion_templates import plan_for
    thesis = {'reviewer': 'test reviewer', 'reviewed_at': '2026-09-04T08:00:00-04:00',
              'direction': 'long', 'horizon_rationale': 'post-event review window',
              'invalidation': 'failure of the reviewed scenario',
              'plan': {'entry_low': 98, 'entry_high': 102, 'stop': 94, 'target': 115, 'horizon_td': 10}}
    p = plan_for('long', 100, 2, {'thesis': thesis})
    assert p['horizon_td'] == 10 and p['stop'] == 94 and p['target'] == 115
    assert p['template']['id'] == 'reviewed_thesis_v1'
    with pytest.raises(ValueError):
        plan_for('short', 100, 2, {'thesis': thesis})


def test_direction_aware_correlation_allows_hedge_but_blocks_shared_exposure(monkeypatch):
    import numpy as np
    returns = np.sin(np.arange(65)) * .01
    close = pd.DataFrame({'A': 100 * np.cumprod(1 + returns), 'B': 100 * np.cumprod(1 + returns)})
    a = {'ticker': 'A', 'direction': 'long', 'score': .9, 'sector': 'X'}
    b = {'ticker': 'B', 'direction': 'long', 'score': .8, 'sector': 'Y'}
    selected, rejected = picks._select([a, b], 10, close)
    assert len(selected) == 1 and 'correlation' in rejected[0]['reason']
    selected, rejected = picks._select([a, {**b, 'direction': 'short'}], 10, close)
    assert len(selected) == 2
    selected, rejected = picks._select([b], 10, close, standing=[a])
    assert not selected


def test_stale_optional_source_cannot_support_a_candidate(tmp_path):
    from advisor.research.candidate_provenance import enrich
    e = {'A': {'detail': {}, 'generators': {'tactical_long': {'rank_pct': .9, 'direction': 'long'},
                                           'cheap_quality': {'rank_pct': .9, 'direction': 'long'}}}}
    health = {}
    enrich(e, tmp_path, {'tactical_long': {'fresh': True}, 'cheap_quality': {'fresh': False}}, health)
    assert list(e['A']['generators']) == ['tactical_long']
    assert 'cheap_quality' in e['A']['stale_evidence']
    assert not health['cheap_quality']['live']


def test_alerts_are_deduplicated_retried_and_recover_once(tmp_path, monkeypatch):
    from advisor.research import suggestion_health as health
    report = {'ok': False, 'reasons': ['stale']}
    monkeypatch.setattr(health, 'assess', lambda *args: report)
    state, messages = {}, []
    health.check_alert(state, tmp_path, lambda msg: False)
    assert not state
    send = lambda msg: messages.append(msg)
    health.check_alert(state, tmp_path, send)
    health.check_alert(state, tmp_path, send)
    assert len(messages) == 1
    report.update(ok=True, reasons=[])
    health.check_alert(state, tmp_path, send)
    health.check_alert(state, tmp_path, send)
    assert len(messages) == 2 and 'recovered' in messages[-1]


def test_ranking_evaluation_includes_unselected_names(env):
    from advisor.research.ranking_evaluation import evaluate
    _slate(env, [_entry('FUND', {'revision_leader': {'rank_pct': .99, 'direction': 'long'}}),
                 _entry('MOMO', {'tactical_long': {'rank_pct': .9, 'direction': 'long'}})])
    result = picks.build(top_n=1)
    idx = pd.bdate_range(result['price_bar'], periods=22)
    close = pd.DataFrame({'FUND': [100.] * 21 + [110.], 'MOMO': [100.] * 21 + [90.]}, index=idx)
    report = evaluate(env, close)
    assert report['comparisons'][0]['n_eligible'] == 2
    assert report['comparisons'][0]['lift_pp'] == pytest.approx(10)
    assert not report['by_scoring_version'][str(picks.SCORING_VERSION)]['promotion_allowed']


def test_terminal_suggestion_groups_render_without_exceptions(env):
    """Run the real fragment in Streamlit's browser-free UI harness."""
    import ast
    from streamlit.testing.v1 import AppTest
    issue(env)
    tree = ast.parse((Path(__file__).parents[1] / 'terminal.py').read_text())
    fragment = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'picks_tab')
    prelude = f'''
import streamlit as st
import json
from pathlib import Path
from advisor.suggestion_policy import lifecycle, release_freshness
RESEARCH = Path({str(env)!r})
AMBER = DIM = RED = GREEN = PANEL_BORDER = 'gray'
BUCKET_COLORS = {{}}
def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {{}}
def panel_header(*args):
    st.write(*args)
def chip(*args):
    return str(args[0])
def esc(value):
    import html
    return html.escape(str(value))
def get_quotes(tickers):
    return {{t: {{'px': 100.}} for t in tickers}}
'''
    app = AppTest.from_string(prelude + '\n' + ast.unparse(fragment) + '\npicks_tab()').run(timeout=10)
    assert not app.exception
    assert any('Waiting / needs research' in h.value for h in app.subheader)
    assert app.dataframe[0].value.iloc[0]['Ticker'] == 'FUND'


def test_nightly_candidate_failure_cannot_issue_old_picks(tmp_path, monkeypatch):
    import time
    from advisor.research import nightly
    monkeypatch.setattr(nightly, 'RESEARCH_DIR', tmp_path)
    monkeypatch.setattr(nightly, 'compute', lambda **kw: {'longs': [], 'shorts': [], 'regime': {}})
    monkeypatch.setattr(nightly, 'render', lambda s: 'fixture')
    monkeypatch.setattr('advisor.research.technicals.build', lambda: {'n_profiled': 0, 'setups': []})
    monkeypatch.setattr('advisor.research.technicals.write', lambda doc: None)
    monkeypatch.setattr('advisor.research.factors_fundamental.compute_scores', lambda: (pd.DataFrame(), {}))
    monkeypatch.setattr('advisor.research.factors_fundamental.render_top', lambda *a: {})
    monkeypatch.setattr('advisor.research.factors_fundamental.write_result', lambda *a: None)
    monkeypatch.setattr('advisor.research.factors_edgar.build', lambda: {'n_eligible': 0})
    monkeypatch.setattr('advisor.research.factors_edgar.write', lambda *a: None)
    monkeypatch.setattr('advisor.research.ic_monitor.mature', lambda: {'n_matured': 0, 'n_new_this_run': 0})
    for name in ('resolve', 'calibrate', 'fit_generator_priors', 'write_record'):
        monkeypatch.setattr(pick_tracker, name, lambda **kw: {})
    monkeypatch.setattr('advisor.research.ranking_evaluation.evaluate', lambda *a: {})
    monkeypatch.setattr('advisor.research.datastore.load_panel', lambda *a: pd.DataFrame())
    monkeypatch.setattr('advisor.research.candidates.main', lambda: (_ for _ in ()).throw(RuntimeError('upstream failed')))
    calls = []
    monkeypatch.setattr(picks, 'build', lambda **kw: calls.append(kw))
    assert nightly._downstream({'build_id': 'fixture'}, None, False, time.time()) == 1
    assert not calls
    status = json.loads((tmp_path / 'nightly_status.json').read_text())
    assert status['status'] == 'failed'
    assert status['stages']['picks'] == 'failed'


def test_mutated_candidate_payload_fails_before_publication(env):
    issue(env)
    path = env / 'candidates_latest.json'
    doc = json.loads(path.read_text())
    doc['slate'][0]['generators']['revision_leader']['rank_pct'] = .4
    path.write_text(json.dumps(doc))
    assert 'digest' in picks.build()['error']


def test_executor_starts_monitor_while_fill_journal_is_blocked(tmp_path, monkeypatch):
    """All broker classes are fakes; this test cannot contact a broker."""
    import sys
    import threading
    import types
    import yaml
    from advisor import executor as ex
    monitor_started = threading.Event()
    reporter_finished = threading.Event()
    journal_entered = threading.Event()
    p = types.SimpleNamespace(id='test', status='APPROVED', expired=lambda: False,
        symbol='SPXW', expiry='2026-09-04', short_strike=6000, long_strike=5995,
        qty=1, limit_price=1., conviction='test', rationale='offline')
    cfg = {'execution': {'enabled': True}, 'limits': {'max_executions_per_day': 1,
           'daily_realized_loss_cap_usd': 600, 'tick_size': .05},
           'paths': {'webull_bot_config': 'broker.yaml'}}
    (tmp_path / 'broker.yaml').write_text(yaml.safe_dump({'account_id': 'OFFLINE',
        'state_file': 'state.json', 'logs_dir': 'logs', 'trade_csv': 'trades.csv', 'stop_multiplier': 2}))
    def transition(pid, status, reason):
        p.status = status
        return p
    monkeypatch.setattr(ex, 'P', types.SimpleNamespace(load=lambda pid: p, load_advisor_cfg=lambda: cfg,
        created_today=lambda *a, **k: [], validate=lambda *a: [], transition=transition, _save=lambda p: None))
    monkeypatch.setattr(ex, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(ex, 'LOCKFILE', str(tmp_path / 'execution.lock'))
    monkeypatch.setattr(ex, '_clock_ok', lambda lim: (True, 'offline'))
    monkeypatch.setattr(ex, '_realized_today_usd', lambda cfg: 0)
    monkeypatch.setenv('ADVISOR_EXECUTION_ENABLED', '1')
    monkeypatch.delenv('WEBULL_DRY_RUN', raising=False)
    state = types.SimpleNamespace(trade_taken_today=False, open_position=None)
    class Engine:
        def __init__(self, *a): pass
        def has_live_position_or_order(self): return False, ''
        def place_spread(self, **kw):
            return types.SimpleNamespace(filled=True, fill_price=1., client_order_id='offline', short_iid='s', long_iid='l')
    class State:
        def __init__(self, *a): pass
        def load(self): return state
        def save(self, state): pass
    class Monitor:
        def __init__(self, **kw): pass
        def run_until_closed(self, state):
            assert journal_entered.wait(1)
            monitor_started.set()
            assert reporter_finished.wait(1)
            return types.SimpleNamespace(reason='offline-close', pnl_usd=0)
    fakes = {'webull_bot.client': {'build_trade_client': lambda: object()},
             'webull_bot.execution': {'ExecutionEngine': Engine},
             'webull_bot.logger': {'BotLogger': lambda **kw: object()},
             'webull_bot.state': {'OpenPosition': types.SimpleNamespace, 'StateStore': State},
             'webull_bot.monitor': {'PositionMonitor': Monitor}}
    for name, attrs in fakes.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    def blocked_journal(row):
        journal_entered.set()
        assert monitor_started.wait(1), 'journal blocked monitor startup'
        reporter_finished.set()
    monkeypatch.setattr(ex, 'journal_add', blocked_journal)
    monkeypatch.setattr(ex.telegram_io, 'send', lambda *a, **kw: True)
    assert ex.execute('test') == 0
    assert monitor_started.is_set()


def test_asof_call_cannot_label_replay_as_live(env):
    doc = issue(env)
    day = doc['price_bar']
    (env / f'candidates_{day}.json').write_text((env / 'candidates_latest.json').read_text())
    result = picks.build(as_of=day)
    assert result['source'] == 'backfill'
    assert result['calibration'] is None
    assert json.loads(picks.OUT.read_text())['source'] == 'live'


def test_executor_calendar_rejects_holiday_and_early_close(monkeypatch):
    from advisor import executor
    monkeypatch.delenv('WEBULL_DRY_RUN', raising=False)
    limits = {'rth_entry_start': '09:35', 'rth_entry_end': '15:00'}
    for stamp in ('2026-09-07T10:00:00-04:00', '2026-11-27T14:00:00-05:00'):
        monkeypatch.setattr(executor, '_now', lambda: datetime.fromisoformat(stamp))
        assert executor._clock_ok(limits)[0] is False


def test_operator_review_to_priority_to_entry_outcome(env, monkeypatch):
    from advisor.research.candidate_provenance import enrich, observe
    from advisor.research.underwriting import record
    now = datetime.now(ET)
    gens = {b: {'rank_pct': .98, 'direction': 'long', 'rank_population_n': 100,
                'rank_population_scope': 'covered'} for b in ('revision_leader', 'cheap_quality')}
    entries = {'FUND': _entry('FUND', gens, {'sector': 'Energy',
               'next_earnings': (now + timedelta(days=60)).date().isoformat()})}
    sources = {b: observe({'as_of': now.isoformat()}) for b in gens}
    enrich(entries, env, sources, {})
    entries['FUND']['selection'] = generators.score_candidate(gens)
    _slate(env, list(entries.values()))
    thesis = {'why_now': 'Fixture catalyst repricing', 'catalyst': 'Fixture event',
              'invalidation': 'Fixture scenario fails', 'horizon_rationale': 'Ten-session review',
              'direction': 'long', 'source_urls': ['https://example.com/fixture'],
              'plan': {'entry_low': 99., 'entry_high': 101., 'stop': 94., 'target': 115., 'horizon_td': 10}}
    record('FUND', thesis, 'offline reviewer', research=env)
    enrich(entries, env, sources, {})
    _slate(env, list(entries.values()))
    monkeypatch.setattr('advisor.research.costs.estimate', lambda ts: {t: {'round_trip_bps': 10.} for t in ts})
    result = picks.build()
    pick = result['picks'][0]
    assert result['n_priority'] == 1
    assert pick['template']['id'] == 'reviewed_thesis_v1'
    assert pick['horizon_td'] == 10 and pick['target'] == 115
    assert lifecycle(pick, 100) == 'entry_zone'
    outcome = simulate(pick, bars([[100, 110, 99, 109], [110, 116, 108, 115]]), matured=False)
    assert outcome['outcome'] == 'target'
    assert outcome['return_pct'] == pytest.approx(14.9)
    assert outcome['calibration_eligible']


def test_later_resolved_revision_cannot_replace_unresolved_first_episode(tmp_path, monkeypatch):
    rows = {'a': {'id': 'a', 'episode_id': 'episode', 'issued_ts': '2026-09-01', 'status': 'open',
                  'scoring_version': picks.SCORING_VERSION, 'source': 'live'},
            'b': {'id': 'b', 'episode_id': 'episode', 'issued_ts': '2026-09-02', 'status': 'resolved',
                  'scoring_version': picks.SCORING_VERSION, 'source': 'live',
                  'calibration_eligible': True, 'win': 1, 'score': .9}}
    monkeypatch.setattr(pick_tracker, 'effective', lambda: rows)
    monkeypatch.setattr(pick_tracker, 'CALIBRATION', tmp_path / 'calibration.json')
    assert pick_tracker.calibrate(False)['n_resolved'] == 0


def test_untimestamped_future_and_close_only_quotes_are_not_entry_evidence():
    from advisor.suggestion_policy import quote_is_fresh
    now = datetime.now(ET)
    assert not quote_is_fresh({'px': 100, 'type': 'delayed'}, now)
    assert not quote_is_fresh({'px': 100, 'ts': (now + timedelta(minutes=1)).isoformat()}, now)
    assert not quote_is_fresh({'px': 100, 'ts': now.isoformat(), 'kind': 'close'}, now)
    assert quote_is_fresh({'px': 100, 'ts': now.isoformat(), 'kind': 'last'}, now)


def test_quote_daemon_tracks_daily_suggestions(env, monkeypatch):
    from advisor import quoted
    issue(env)
    monkeypatch.setattr(quoted, '_data', lambda: env.parent / 'quotes')
    assert 'FUND' in quoted.watch_symbols()


def test_slow_yahoo_fallback_does_not_block_quote_publication(monkeypatch):
    import threading
    from advisor.quoted import Daemon
    release, entered = threading.Event(), threading.Event()
    daemon = Daemon()
    def slow(symbol):
        entered.set()
        release.wait(2)
        return {'px': 100, 'market_ts': datetime.now(ET).isoformat()}
    monkeypatch.setattr(daemon, '_fetch_yahoo', slow)
    try:
        daemon.yf_fill(['A', 'B', 'C', 'D', 'E'])
        assert entered.wait(1)
        assert len(daemon._yf_jobs) == 4
        snap = daemon.snapshot(['A'])
        assert snap['quotes'] == {}  # publication remains responsive while fetches wait
    finally:
        release.set()
        daemon._yf_pool.shutdown(wait=True)


def test_quote_subscriptions_cancel_removed_symbols():
    from types import SimpleNamespace
    from advisor.quoted import Daemon
    daemon = Daemon()
    canceled = []
    daemon.ib = SimpleNamespace(cancelMktData=lambda contract: canceled.append(contract))
    daemon.tickers = {'OLD': SimpleNamespace(contract='old-contract')}
    daemon.last_update_mono = {'OLD': 1}
    daemon.last_update_ts = {'OLD': 'old'}
    daemon.subscribe([])
    assert canceled == ['old-contract']
    assert not daemon.tickers and not daemon.last_update_ts


def test_volume_update_cannot_freshen_an_old_last_quote(monkeypatch):
    from types import SimpleNamespace
    from advisor.quoted import Daemon
    d = Daemon()
    clock = {'now': 1000.}
    monkeypatch.setattr('advisor.quoted.time.monotonic', lambda: clock['now'])
    d.record_price_update('A', SimpleNamespace(ticks=[SimpleNamespace(tickType=4, price=100.)]))
    assert d.ib_quote_fresh('A')
    clock['now'] += 121
    d.record_price_update('A', SimpleNamespace(ticks=[SimpleNamespace(tickType=8, price=100.)]))
    assert not d.ib_quote_fresh('A')
    d.record_price_update('A', SimpleNamespace(ticks=[SimpleNamespace(tickType=1, price=101.),
                                                    SimpleNamespace(tickType=2, price=103.)]))
    assert d._ib_price('A')[:2] == (102., 'mid')


def test_concurrent_release_writers_preserve_every_committed_issue(tmp_path):
    """Real OS locks and separate processes; no lost update under contention."""
    import subprocess
    import sys
    worker = r'''
import sys
from pathlib import Path
from advisor.research.suggestion_store import writer_lock, commit
root, worker = Path(sys.argv[1]), sys.argv[2]
for i in range(10):
    with writer_lock(root):
        row = {'id': f'{worker}-{i}', 'type': 'pick'}
        commit(root, root/'picks_latest.json', root/'ledger.jsonl',
               {'as_of': '2026-09-04T08:00:00-04:00', 'picks': [row]}, [row])
'''
    processes = [subprocess.Popen([sys.executable, '-c', worker, str(tmp_path), str(i)],
                                  cwd=Path(__file__).resolve().parents[2],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(4)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
    assert len(store.committed_ids(tmp_path)) == 40
    assert len({r['id'] for r in store.read_rows(tmp_path / 'ledger.jsonl')}) == 40


def test_holiday_quotes_keep_prior_session_context_but_not_live_status():
    from advisor.quoted import yf_quote_fresh, market_open
    now = datetime.fromisoformat('2026-09-07T12:00:00-04:00')
    assert not market_open(now)
    assert yf_quote_fresh({'market_ts': '2026-09-04T16:00:00-04:00'}, now)
