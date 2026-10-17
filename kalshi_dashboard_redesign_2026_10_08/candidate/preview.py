"""Read-only Kalshi dashboard entry point. Trading runs in its own service."""
import importlib.util
import sys
from pathlib import Path
import streamlit as st

st.set_page_config(page_title='Kalshi Desk · Overview', page_icon='◈', layout='wide')
sys.path.append('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
PANEL = Path(__file__).with_name('dashboard_panel.py')

@st.fragment(run_every='10s')
def dashboard():
    panel_directory = str(PANEL.parent)
    if panel_directory not in sys.path:
        sys.path.insert(0, panel_directory)
    spec = importlib.util.spec_from_file_location('crypto_dashboard_panel', PANEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render(st)

dashboard()
