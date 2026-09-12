from pathlib import Path


def test_watchlist_period_selector_uses_stable_string_radio_contract():
    source=(Path(__file__).resolve().parents[1]/'watchlist_workspace.py').read_text()
    assert 'st.radio("Chart period",["1W","1M","3M"]' in source
    assert 'segmented_control("Chart period"' not in source
