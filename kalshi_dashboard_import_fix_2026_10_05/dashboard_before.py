"""Read-only crypto dashboard. Legacy display archived September 12, 2026."""
import importlib.util
from pathlib import Path
import streamlit as st

st.set_page_config(page_title='Kalshi · Crypto 15m', page_icon='₿', layout='wide')
PANEL=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/dashboard_panel.py')

@st.fragment(run_every='10s')
def dashboard():
    spec=importlib.util.spec_from_file_location('crypto_dashboard_panel',PANEL)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render(st)

dashboard()
